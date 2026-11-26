#![no_std]
#![no_main]

use ckb_std::{default_alloc, entry};

entry!(main);
default_alloc!();

fn main() -> i8 {
    use quotacell_metatype_experiment::{composed, group_matches};
    ckb_metatype_contracts::with_invocation(|invocation, resolver| {
        let group = invocation.group;
        group_matches(&group.input_indices, &group.output_indices)?;
        composed(group.script, resolver)
    })
}
