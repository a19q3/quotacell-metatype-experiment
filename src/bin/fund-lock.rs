#![no_std]
#![no_main]

use ckb_std::{default_alloc, entry};

entry!(main);
default_alloc!();

fn main() -> i8 {
    quotacell_metatype_experiment::protected_funds(false).map_or(51, |_| 0)
}
