#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export CARGO_TARGET_DIR="$PWD/target"
export RUSTFLAGS="-C passes=lower-atomic -C target-feature=-c"
target=riscv64imac-unknown-none-elf
release="$CARGO_TARGET_DIR/$target/release"
command=(cargo build --locked --release --target "$target" --features riscv-binaries)
empty_hash=$(printf '%064d' 0)
POCKET_FUND_CODE=$empty_hash POCKET_COMPOSED_FUND_CODE=$empty_hash "${command[@]}" --bin fund-lock --bin composed-fund-lock
hash_file() {
    python3 - "$1" <<'PYHASH'
import hashlib
from pathlib import Path
import sys

print(hashlib.blake2b(Path(sys.argv[1]).read_bytes(), digest_size=32, person=b"ckb-default-hash").hexdigest())
PYHASH
}
export POCKET_FUND_CODE POCKET_COMPOSED_FUND_CODE
POCKET_FUND_CODE=$(hash_file "$release/fund-lock")
POCKET_COMPOSED_FUND_CODE=$(hash_file "$release/composed-fund-lock")
"${command[@]}" --bin quota-type --bin quota-adapter --bin payment-cap
python3 - "$release" <<'PY'
import hashlib
import json
from pathlib import Path
import sys

directory = Path(sys.argv[1])
records = {}
for name in ("fund-lock", "composed-fund-lock", "quota-type", "quota-adapter", "payment-cap"):
    data = (directory / name).read_bytes()
    records[name] = {
        "bytes": len(data),
        "ckb_data_hash": "0x" + hashlib.blake2b(data, digest_size=32, person=b"ckb-default-hash").hexdigest(),
        "sha256": hashlib.sha256(data).hexdigest(),
    }
print(json.dumps(records, indent=2))
PY
