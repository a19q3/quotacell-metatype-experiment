#![no_std]

use ckb_metatype::LogicalScript;
use ckb_metatype_contracts::{ManifestResolver, bounded_data};
use ckb_std::{
    ckb_constants::Source,
    ckb_types::prelude::*,
    error::SysError,
    high_level::{
        load_cell_capacity, load_cell_lock, load_cell_lock_hash, load_cell_type_hash, load_input,
        load_script, load_script_hash,
    },
};

pub const QUOTA_CODE: [u8; 32] = [0x51; 32];
pub const CAP_CODE: [u8; 32] = [0x52; 32];
const MAX_FUNDS: usize = 64;
const MAX_FEE: u64 = 1_000_000;
const DATA2: u8 = 4;
type Hash = [u8; 32];

#[cfg(target_arch = "riscv64")]
const FUND_CODES: [Hash; 2] = [
    hex_hash(env!("POCKET_FUND_CODE")),
    hex_hash(env!("POCKET_COMPOSED_FUND_CODE")),
];
#[cfg(not(target_arch = "riscv64"))]
const FUND_CODES: [Hash; 2] = [[0; 32]; 2];

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct Grant {
    actor: Hash,
    recipient: Hash,
    limit: u64,
    used: u64,
    per_payment: u64,
}

#[derive(Clone, Debug, Eq, PartialEq)]
struct State {
    owner: Hash,
    grants: [Grant; 2],
    shared_limit: u64,
    shared_used: u64,
    enabled: u8,
}

fn take<const N: usize>(tail: &mut &[u8]) -> Option<[u8; N]> {
    let (field, rest) = tail.split_first_chunk::<N>()?;
    *tail = rest;
    Some(*field)
}

fn amount(tail: &mut &[u8]) -> Option<u64> {
    Some(u64::from_le_bytes(take(tail)?))
}

impl State {
    const SIZE: usize = 225;

    fn decode(mut tail: &[u8]) -> Option<Self> {
        let owner = take(&mut tail)?;
        let mut grant = || {
            Some(Grant {
                actor: take(&mut tail)?,
                recipient: take(&mut tail)?,
                limit: amount(&mut tail)?,
                used: amount(&mut tail)?,
                per_payment: amount(&mut tail)?,
            })
        };
        let grants = [grant()?, grant()?];
        let shared_limit = amount(&mut tail)?;
        let shared_used = amount(&mut tail)?;
        let [enabled] = tail else { return None };
        let [a, b] = grants;
        let parties = [owner, a.actor, b.actor, a.recipient, b.recipient];
        if parties
            .iter()
            .enumerate()
            .any(|(i, party)| *party == [0; 32] || parties[..i].contains(party))
            || *enabled & !3 != 0
            || a.used.checked_add(b.used)? != shared_used
            || shared_used > shared_limit
            || grants.iter().any(|g| {
                g.used > g.limit
                    || g.limit > shared_limit
                    || g.per_payment == 0
                    || g.per_payment > g.limit
            })
        {
            return None;
        }
        Some(Self {
            owner,
            grants,
            shared_limit,
            shared_used,
            enabled: *enabled,
        })
    }
}

fn valid(condition: bool) -> Option<()> {
    condition.then_some(())
}

fn is_plain(index: usize, source: Source) -> Option<()> {
    valid(load_cell_type_hash(index, source).ok()?.is_none())?;
    valid(bounded_data(index, source, 0).ok()?.is_empty())
}

fn plain_at_lock(index: usize, source: Source, lock: &Hash) -> Option<()> {
    is_plain(index, source)?;
    valid(load_cell_lock_hash(index, source).ok()? == *lock)
}

fn group_present(source: Source) -> Option<bool> {
    match (load_cell_capacity(0, source), load_cell_capacity(1, source)) {
        (Ok(_), Err(SysError::IndexOutOfBound)) => Some(true),
        (Err(SysError::IndexOutOfBound), Err(SysError::IndexOutOfBound)) => Some(false),
        _ => None,
    }
}

pub fn group_matches(inputs: &[u16], outputs: &[u16]) -> Option<()> {
    for (indices, source) in [(inputs, Source::GroupInput), (outputs, Source::GroupOutput)] {
        let expected: &[u16] = if group_present(source)? { &[0] } else { &[] };
        valid(indices == expected)?;
    }
    Some(())
}

fn count(source: Source, limit: usize) -> Option<usize> {
    for index in 0..=limit {
        match load_cell_capacity(index, source) {
            Ok(_) if index < limit => (),
            Err(SysError::IndexOutOfBound) => return Some(index),
            _ => return None,
        }
    }
    None
}

fn capacity(index: usize, source: Source) -> Option<u64> {
    load_cell_capacity(index, source).ok()
}

fn sum(source: Source, mut indices: core::ops::Range<usize>) -> Option<u64> {
    indices.try_fold(0_u64, |total, index| {
        total.checked_add(capacity(index, source)?)
    })
}

fn kept_policy_lock(index: usize, source: Source, policy: &Hash, owner: &Hash) -> Option<Hash> {
    let lock = load_cell_lock(index, source).ok()?;
    let args = lock.args().raw_data();
    valid(u8::from(lock.hash_type()) == DATA2)?;
    let policy_script = load_script().ok()?;
    let composed = policy_script.args().raw_data().len() != 32;
    valid(lock.code_hash().as_slice() == FUND_CODES[usize::from(composed)])?;
    valid(args.len() == 64 && args[..32] == *policy && args[32..] == *owner)?;
    load_cell_lock_hash(index, source).ok()
}

#[cfg(target_arch = "riscv64")]
const fn hex_hash(hex: &str) -> [u8; 32] {
    const fn nibble(byte: u8) -> u8 {
        match byte {
            b'0'..=b'9' => byte - b'0',
            b'a'..=b'f' => byte - b'a' + 10,
            _ => panic!("invalid byte in the fund Lock hash"),
        }
    }
    let bytes = hex.as_bytes();
    assert!(bytes.len() == 64);
    let mut hash = [0; 32];
    let mut i = 0;
    while i < 32 {
        hash[i] = nibble(bytes[i * 2]) * 16 + nibble(bytes[i * 2 + 1]);
        i += 1;
    }
    hash
}

pub fn protected_funds(composed: bool) -> Option<()> {
    let script = load_script().ok()?;
    let args = script.args().raw_data();
    let (policy, owner) = args.split_first_chunk::<32>()?;
    let owner: &Hash = owner.try_into().ok()?;
    let actor = |i| plain_at_lock(i, Source::Input, owner).is_some();
    if (0..=MAX_FUNDS + 1).any(actor) {
        return Some(());
    }

    let input_type = ckb_std::high_level::load_cell_type(0, Source::Input).ok()??;
    valid(input_type.calc_script_hash().as_slice() == policy)?;
    valid(load_cell_lock_hash(0, Source::Input).ok()? == load_script_hash().ok()?)?;
    valid(u8::from(input_type.hash_type()) == DATA2)?;
    match input_type.args().raw_data().len() {
        32 if !composed => Some(()),
        96 if composed => {
            let mut resolver = ManifestResolver::default();
            let data = resolver.manifest_data(0, Source::Input)?;
            let manifest = data.view()?;
            valid(manifest.entries().iter().any(|entry| {
                entry.script.code() == (&QUOTA_CODE, 0) && entry.script.args().is_empty()
            }))
        }
        _ => None,
    }
}

pub fn conventional() -> Option<()> {
    let script = load_script().ok()?;
    valid(u8::from(script.hash_type()) == DATA2)?;
    let args = script.args().raw_data();
    let namespace = args.as_ref().try_into().ok()?;
    let load = |source| State::decode(&bounded_data(0, source, State::SIZE).ok()?);
    policy(namespace, load)
}

pub fn composed(logical: LogicalScript<'_>, manifests: &mut ManifestResolver) -> Option<()> {
    valid(logical.code() == (&QUOTA_CODE, 0) && logical.args().is_empty())?;
    let script = load_script().ok()?;
    let args = script.args().raw_data();
    let deployment = ckb_metatype::MetatypeDeployment::decode(&args)?;
    policy(&deployment.namespace, |source| {
        let raw = manifests.manifest_data(0, source)?;
        State::decode(raw.view()?.state_for(logical)?)
    })
}

fn policy(namespace: &Hash, mut load: impl FnMut(Source) -> Option<State>) -> Option<()> {
    let policy = load_script_hash().ok()?;
    let has_input = group_present(Source::GroupInput)?;
    let has_output = group_present(Source::GroupOutput)?;
    for (present, source) in [(has_input, Source::Input), (has_output, Source::Output)] {
        if present {
            valid(load_cell_type_hash(0, source).ok()? == Some(policy))?;
        }
    }
    let inputs = count(Source::Input, MAX_FUNDS + 2)?;
    let outputs = count(Source::Output, 4)?;
    let fee = sum(Source::Input, 0..inputs)?.checked_sub(sum(Source::Output, 0..outputs)?)?;
    valid(fee <= MAX_FEE)?;

    if !has_input {
        let state = load(Source::Output)?;
        kept_policy_lock(0, Source::Output, &policy, &state.owner)?;
        let mut hash = ckb_hash::new_blake2b();
        hash.update(load_input(0, Source::Input).ok()?.as_slice());
        hash.update(&0u64.to_le_bytes());
        let mut genesis = [0; 32];
        hash.finalize(&mut genesis);
        valid(
            genesis == *namespace && inputs == 1 && state.shared_used == 0 && state.enabled == 3,
        )?;
        plain_at_lock(0, Source::Input, &state.owner)?;
        return change(1, outputs, &state.owner);
    }

    let old = load(Source::Input)?;
    let fund_lock = kept_policy_lock(0, Source::Input, &policy, &old.owner)?;
    let actor = load_cell_lock_hash(1, Source::Input).ok()?;
    plain_at_lock(1, Source::Input, &actor)?;
    for index in 2..inputs {
        plain_at_lock(index, Source::Input, &fund_lock)?;
    }
    let protected = sum(Source::Input, 2..inputs)?;

    if !has_output {
        valid(actor == old.owner && outputs == 1)?;
        plain_at_lock(0, Source::Output, &old.owner)?;
        return valid(
            capacity(0, Source::Output)? >= capacity(0, Source::Input)?.checked_add(protected)?,
        );
    }
    let new = load(Source::Output)?;
    kept_policy_lock(0, Source::Output, &policy, &old.owner)?;
    valid(capacity(0, Source::Output)? == capacity(0, Source::Input)?)?;

    if actor == old.owner {
        valid(inputs == 2 && old.owner == new.owner && old.shared_used == new.shared_used)?;
        valid(new.shared_limit >= old.shared_limit)?;
        for (before, after) in old.grants.into_iter().zip(new.grants) {
            valid(
                after.limit >= before.limit
                    && Grant {
                        limit: after.limit,
                        ..before
                    } == after,
            )?;
        }
        return change(1, outputs, &actor);
    }

    let delegate = old.grants.iter().position(|grant| grant.actor == actor)?;
    valid(inputs >= 3 && old.enabled & (1 << delegate) != 0)?;
    let grant = old.grants[delegate];
    plain_at_lock(1, Source::Output, &fund_lock)?;
    plain_at_lock(2, Source::Output, &grant.recipient)?;
    let payment = capacity(2, Source::Output)?;
    valid(payment != 0 && payment <= grant.per_payment)?;
    valid(capacity(1, Source::Output)?.checked_add(payment)? == protected)?;
    let mut expected = old;
    expected.grants[delegate].used = grant.used.checked_add(payment)?;
    expected.shared_used = expected.shared_used.checked_add(payment)?;
    valid(expected == new)?;
    change(3, outputs, &actor)
}

fn change(start: usize, outputs: usize, actor: &Hash) -> Option<()> {
    match outputs.checked_sub(start)? {
        0 => Some(()),
        1 => plain_at_lock(start, Source::Output, actor),
        _ => None,
    }
}

pub fn payment_cap(
    logical: LogicalScript<'_>,
    inputs: &[u16],
    outputs: &[u16],
    manifests: &mut ManifestResolver,
) -> Option<()> {
    group_matches(inputs, outputs)?;
    valid(logical.code() == (&CAP_CODE, 0))?;
    let max = u64::from_le_bytes(logical.args().try_into().ok()?);
    for (indices, source) in [(inputs, Source::Input), (outputs, Source::Output)] {
        if let [index] = indices {
            let data = manifests.manifest_data(*index, source)?;
            let manifest = data.view()?;
            valid(manifest.state_for(logical)?.is_empty())?;
            valid(manifest.entries().iter().any(|entry| {
                entry.script.code() == (&QUOTA_CODE, 0) && entry.script.args().is_empty()
            }))?;
        }
    }
    if !inputs.is_empty() && !outputs.is_empty() && count(Source::Output, 4)? >= 3 {
        valid(capacity(2, Source::Output)? <= max)?;
    }
    Some(())
}
