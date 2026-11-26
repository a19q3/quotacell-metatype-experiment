#![no_std]
#![no_main]

use ckb_std::{default_alloc, entry};

entry!(main);
default_alloc!();

fn main() -> i8 {
    quotacell_metatype_experiment::conventional().map_or(52, |_| 0)
}
