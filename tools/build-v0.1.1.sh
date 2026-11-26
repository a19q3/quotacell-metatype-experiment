#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export CARGO_TARGET_DIR="$PWD/target"
export RUSTFLAGS="-C passes=lower-atomic -C target-feature=-c"
target=riscv64imac-unknown-none-elf
mkdir -p "$CARGO_TARGET_DIR"

# The original workspace keeps its version, lockfile and source so the quota ELF can be reproduced.
tools/build-contracts.sh > "$CARGO_TARGET_DIR/legacy-contracts.json"
diff -u evidence/contracts.json "$CARGO_TARGET_DIR/legacy-contracts.json"
cargo build --locked --release --manifest-path adapters/payment-cap/Cargo.toml --target "$target"

python3 - "$CARGO_TARGET_DIR" "$target" <<'PY'
import hashlib
import json
from pathlib import Path
import sys

directory = Path(sys.argv[1])
manifest = json.loads((directory / "legacy-contracts.json").read_text())
name = "quotacell-payment-cap"
data = (directory / sys.argv[2] / "release" / name).read_bytes()
manifest[name] = {
    "bytes": len(data),
    "ckb_data_hash": "0x" + hashlib.blake2b(data, digest_size=32, person=b"ckb-default-hash").hexdigest(),
    "sha256": hashlib.sha256(data).hexdigest(),
}
print(json.dumps(manifest, indent=2))
PY
