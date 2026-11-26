use ckb_metatype::{Hash, MAX_TRANSACTION_CELLS_PER_SIDE};
use ckb_metatype_contracts::bounded_data;
use ckb_std::{
    ckb_constants::Source,
    error::SysError,
    high_level::{load_cell_capacity, load_cell_lock_hash, load_cell_type_hash},
};

const MAX_CELLS: usize = MAX_TRANSACTION_CELLS_PER_SIDE;

/// Transaction-local roles. Positions are meaningful only for the transaction
/// carrying them. Both encodings validate exactly the same roles.
pub struct PaymentRefs {
    pub policy_input: u16,
    pub policy_successor: u16,
    pub protected_change: u16,
    pub payment: u16,
}

impl PaymentRefs {
    pub const FIXED: Self = Self {
        policy_input: 0,
        policy_successor: 0,
        protected_change: 1,
        payment: 2,
    };

    pub fn decode(raw: &[u8]) -> Option<Self> {
        let &[b'P', 1, a, b, c, d, e, f, g, h] = raw else {
            return None;
        };
        Some(Self {
            policy_input: u16::from_le_bytes([a, b]),
            policy_successor: u16::from_le_bytes([c, d]),
            protected_change: u16::from_le_bytes([e, f]),
            payment: u16::from_le_bytes([g, h]),
        })
    }
}

pub struct Context {
    pub actor: Hash,
    pub fund_lock: Hash,
    pub protected: u64,
}

pub fn physical_index(source: Source, deployment: &Hash) -> Option<Option<u16>> {
    let mut found = None;
    for index in 0..=MAX_CELLS {
        match load_cell_type_hash(index, source) {
            Ok(ty) if index < MAX_CELLS => {
                if ty.as_ref() == Some(deployment) && found.replace(index as u16).is_some() {
                    return None;
                }
            }
            Err(SysError::IndexOutOfBound) => return Some(found),
            _ => return None,
        }
    }
    None
}

pub fn context(policy_input: u16, fund_lock: Hash) -> Option<Context> {
    let mut actor = None;
    let mut protected = 0u64;
    for index in 0..=MAX_CELLS {
        match load_cell_capacity(index, Source::Input) {
            Ok(capacity) if index < MAX_CELLS => {
                if index == usize::from(policy_input) {
                    continue;
                }
                plain(index, Source::Input)?;
                let lock = load_cell_lock_hash(index, Source::Input).ok()?;
                if lock == fund_lock {
                    protected = protected.checked_add(capacity)?;
                } else if actor.replace(lock).is_some() {
                    return None;
                }
            }
            Err(SysError::IndexOutOfBound) => {
                return Some(Context {
                    actor: actor?,
                    fund_lock,
                    protected,
                });
            }
            _ => return None,
        }
    }
    None
}

fn plain(index: usize, source: Source) -> Option<()> {
    (load_cell_type_hash(index, source).ok()?.is_none()
        && bounded_data(index, source, 0).ok()?.is_empty())
    .then_some(())
}

pub fn validate_payment(
    refs: PaymentRefs,
    input: u16,
    output: u16,
    roles: Context,
    limit: u64,
) -> Option<()> {
    if refs.policy_input != input
        || refs.policy_successor != output
        || refs.protected_change == output
        || refs.payment == output
        || refs.payment == refs.protected_change
    {
        return None;
    }
    let mut change = None;
    let mut payment = None;
    let mut actor_change = false;
    for index in 0..=MAX_CELLS {
        match load_cell_capacity(index, Source::Output) {
            Ok(capacity) if index < MAX_CELLS => {
                let role = index as u16;
                if role == output {
                    continue;
                }
                plain(index, Source::Output)?;
                let lock = load_cell_lock_hash(index, Source::Output).ok()?;
                if role == refs.protected_change && lock == roles.fund_lock {
                    change = Some(capacity);
                } else if role == refs.payment && lock != roles.fund_lock && lock != roles.actor {
                    payment = Some(capacity);
                } else if role != refs.protected_change
                    && role != refs.payment
                    && lock == roles.actor
                    && !actor_change
                {
                    actor_change = true;
                } else {
                    return None;
                }
            }
            Err(SysError::IndexOutOfBound) => {
                let amount = payment?;
                return (amount > 0
                    && amount <= limit
                    && change?.checked_add(amount)? == roles.protected)
                    .then_some(());
            }
            _ => return None,
        }
    }
    None
}
