#![no_std]
#![no_main]

use ckb_std::{default_alloc, entry};

entry!(main);
default_alloc!();

fn main() -> i8 {
    ckb_metatype_contracts::with_invocation(|invocation, resolver| {
        let group = invocation.group;
        quotacell_metatype_experiment::payment_cap(
            group.script,
            &group.input_indices,
            &group.output_indices,
            resolver,
        )
    })
}
