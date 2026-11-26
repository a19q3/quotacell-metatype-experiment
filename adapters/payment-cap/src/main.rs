#![no_std]
#![no_main]

mod payment;

use ckb_metatype::{Constraint, Invocation, LogicalScript, ManifestChange, MetatypeManifest};
use ckb_metatype_contracts::{ManifestResolver, with_invocation};
use ckb_std::{
    ckb_constants::Source,
    default_alloc, entry,
    high_level::{load_cell_lock, load_cell_lock_hash},
};

use payment::{PaymentRefs, context, physical_index, validate_payment};

entry!(main);
default_alloc!();

const QUOTA_CODE: [u8; 32] = [0x51; 32];
const CAP_CODE: [u8; 32] = [0x52; 32];

#[derive(Copy, Clone)]
enum ReferenceSource {
    Fixed,
    SuccessorState,
    Witness,
}

impl ReferenceSource {
    fn args(script: LogicalScript<'_>) -> Option<(Self, u64)> {
        if script.code() != (&CAP_CODE, 0) {
            return None;
        }
        let (selector, limit) = script.args().split_last_chunk::<8>()?;
        let source = match selector {
            [] => Self::Fixed,
            [1] => Self::SuccessorState,
            [2] => Self::Witness,
            _ => return None,
        };
        let limit = u64::from_le_bytes(*limit);
        (limit > 0).then_some((source, limit))
    }

    fn previous_state(self, raw: &[u8]) -> Option<()> {
        (raw.is_empty()
            || matches!(self, Self::SuccessorState) && PaymentRefs::decode(raw).is_some())
        .then_some(())
    }

    fn references(self, raw: &[u8], invocation: &Invocation<'_>) -> Option<PaymentRefs> {
        match (self, raw, invocation.witness) {
            (Self::Fixed, [], None) => Some(PaymentRefs::FIXED),
            (Self::SuccessorState, raw, None) => PaymentRefs::decode(raw),
            (Self::Witness, [], Some(witness))
                if witness.action == 1 && witness.proof.is_empty() && witness.seal.is_empty() =>
            {
                PaymentRefs::decode(witness.message)
            }
            _ => None,
        }
    }
}

fn main() -> i8 {
    with_invocation(validate)
}

fn quota(manifest: &MetatypeManifest<'_>) -> Option<()> {
    manifest
        .entries()
        .iter()
        .any(|entry| entry.script.code() == (&QUOTA_CODE, 0) && entry.script.args().is_empty())
        .then_some(())
}

fn validate(invocation: Invocation<'_>, resolver: &mut ManifestResolver) -> Option<()> {
    let group = &invocation.group;
    let (source, limit) = ReferenceSource::args(group.script)?;
    let input = physical_index(Source::Input, invocation.deployment_type)?;
    let output = physical_index(Source::Output, invocation.deployment_type)?;
    let virtual_input = group.input_indices.as_slice();
    let virtual_output = group.output_indices.as_slice();

    let old_data = match input {
        Some(index) => Some(resolver.manifest_data(index, Source::Input)?),
        None => None,
    };
    let new_data = match output {
        Some(index) => Some(resolver.manifest_data(index, Source::Output)?),
        None => None,
    };
    let (old, new) = match (&old_data, &new_data) {
        (Some(old), Some(new)) => (Some(old.view()?), Some(new.view()?)),
        (Some(old), None) => (Some(old.view()?), None),
        (None, Some(new)) => (None, Some(new.view()?)),
        (None, None) => return None,
    };
    if let Some(manifest) = &old {
        quota(manifest)?;
    }
    if let Some(manifest) = &new {
        quota(manifest)?;
    }

    let no_message = invocation.witness.is_none();
    match (input, output, old, new) {
        // Issuance is authorised by the original Quota rule.
        (None, Some(output), None, Some(new))
            if virtual_input.is_empty() && virtual_output == [output] =>
        {
            (new.state_for(group.script)?.is_empty() && no_message).then_some(())
        }
        // Retirement is authorised by the original Quota rule and consumes the
        // physical policy. An input-only group cannot disappear into a successor.
        (Some(input), None, Some(old), None)
            if virtual_input == [input] && virtual_output.is_empty() =>
        {
            source.previous_state(old.state_for(group.script)?)?;
            no_message.then_some(())
        }
        (Some(input), Some(output), Some(old), Some(new)) if virtual_output == [output] => {
            let fund_lock = load_cell_lock_hash(usize::from(input), Source::Input).ok()?;
            (load_cell_lock_hash(usize::from(output), Source::Output).ok()? == fund_lock)
                .then_some(())?;
            let roles = context(input, fund_lock)?;
            let cap_state = new.state_for(group.script)?;
            if virtual_input.is_empty() {
                let policy_lock = load_cell_lock(usize::from(input), Source::Input).ok()?;
                let args = policy_lock.args().raw_data();
                let (policy, owner) = args.split_first_chunk::<32>()?;
                let expected = ManifestChange::Attached(Constraint {
                    script: group.script,
                    state: &[],
                });
                return (policy == invocation.deployment_type
                    && owner == roles.actor
                    && roles.protected == 0
                    && no_message
                    && old.diff(&new) == [expected])
                .then_some(());
            }
            (virtual_input == [input]).then_some(())?;
            source.previous_state(old.state_for(group.script)?)?;
            if roles.protected == 0 {
                return (cap_state.is_empty() && no_message).then_some(());
            }
            let references = source.references(cap_state, &invocation)?;
            validate_payment(references, input, output, roles, limit)
        }
        _ => None,
    }
}
