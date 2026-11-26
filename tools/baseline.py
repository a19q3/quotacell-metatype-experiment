"""Retrieve and verify the exact public QuotaCell QCN4 and provider binaries."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess

GENESIS = "0x10639e0895502b5688a6be8cf69460d76541bfa4821629d86d62ba0aae3f9606"
DEPLOYMENTS = {
    "0xb4f171c9c9caf7401f54a8e56225ae21d95032150a87a4678eac3f66a3137b93": {"A": 0, "F": 1},
    "0xd6654da12e92d44a37b9c48a7b27db325fa3e68cc50f3e17ce74a791523c00d0": {"Q": 0},
}
BASELINE_SHA = {
    "A": "f4ba47fa9a540a74efcee48829dc0cf8b5b9dbba6950ea118aa5d47c42e60d30",
    "F": "672b6d8bf809cbcf8e2ec440da4d6aae92241b7c298c3c45a33c3f179a032dba",
    "Q": "f636bb5a9ddb7db41c73ab263a5b5483acae2e36c53afca2257a1cbc95d83997",
}


def read_rpc(args, rpc_method, parameter):
    request = json.dumps(dict(jsonrpc="2.0", id=1, method=rpc_method, params=[parameter]),
                         separators=(",", ":"))
    network = ["--noproxy", "*"] if args.direct else []
    body = subprocess.check_output(["curl", "--fail", "--silent", "--show-error", "--max-time", "30",
                                   *network, "--header", "Content-Type: application/json",
                                   "--data", request, args.rpc], text=True)
    response = json.loads(body)
    if "error" in response:
        raise RuntimeError(f"{rpc_method} rejected by the read-only RPC: {response['error']}")
    return response["result"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--rpc", default="https://testnet.ckb.dev")
    parser.add_argument("--direct", action="store_true", help="Disable the configured HTTP proxy for reads")
    args = parser.parse_args()
    if read_rpc(args, "get_block_hash", "0x0") != GENESIS:
        parser.error("RPC returned an unexpected genesis hash")
    result = {}
    for tx_id, records in DEPLOYMENTS.items():
        tx = read_rpc(args, "get_transaction", tx_id)
        if tx["tx_status"]["status"] != "committed":
            parser.error(f"Deployment {tx_id} is unavailable")
        for role, index in records.items():
            raw = bytes.fromhex(tx["transaction"]["outputs_data"][index].removeprefix("0x"))
            if hashlib.sha256(raw).hexdigest() != BASELINE_SHA[role]:
                parser.error(f"{role} bytes differ from the published SHA-256")
            out = args.directory / role
            if out.exists() and out.read_bytes() != raw:
                parser.error(f"Refusing to replace different bytes at {out}")
            result[out] = raw
    args.directory.mkdir(parents=True, exist_ok=True)
    for out, raw in result.items():
        if not out.exists():
            with out.open("xb") as stream:
                stream.write(raw)
        print(f"{out.name} {len(raw)} bytes {BASELINE_SHA[out.name]}")


if __name__ == "__main__":
    main()
