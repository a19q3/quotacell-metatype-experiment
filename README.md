# QuotaCell and Metatype experiment

An existing CKB Pocket can acquire an independently enforced payment cap after issuance. This experiment follows the Pocket from its first delegated payment through attachment, further payments and owner closure. It compares that sequence with the original native QuotaCell profile and conventional CKB Lock/Type composition. It is experimental and unaudited.

| Profile | Policy while active | Late recovery |
| --- | --- | --- |
| A | Original QuotaCell QCN4/QCT4 | Owner and terminal policy |
| B | Conventional CKB | Independent owner Lock authority |
| C | Metatype with Quota | Independent owner Lock authority |
| D | Metatype with Quota and Cap from issuance | Independent owner Lock authority |
| E | Metatype with Cap attached after issuance | Independent owner Lock authority |

```mermaid
flowchart LR
    A["Issue Pocket with Quota only"] --> P["Delegate pays 61 CKB"]
    P --> T["Owner attaches Cap of 61 CKB"]
    T --> Q["Quota and Cap independently validate payments"]
    Q --> R["Owner retires policy"]
```

Profile E admits both adapter mappings at deployment. Its initial Manifest contains just Quota. A 62 CKB candidate payment passes before attachment and fails afterwards. The owner attaches the cap without changing the physical Type, Pocket namespace, fund Lock, authorised delegates or any byte of quota state. The cap receives an output-only group and checks owner authorisation and the Manifest diff. The historical Quota adapter and dispatcher ELFs are reused byte for byte.

An immutable table fixes which logical code/addressing pairs a deployment supports. Attaching a supported rule changes a Cell's Manifest. Introducing a new mapping changes the committed table and physical deployment. The example retains the existing Pocket identity across attachment, with a newly created policy outpoint.

See [v0.1.1 results and charts](RESULTS-v0.1.1.md), the [payment interface experiment](PAYMENT-INTERFACE.md), and the [security model](SECURITY.md). [Historical v0.1.0 results](RESULTS.md) preserve the earlier workload and measurements.

## Reproduce

Use Rust 1.95.0, the RISC-V target, Python 3, curl, GitHub CLI and CKB 0.210.0 with the IntegrationTest and Experiment RPC modules. The experiment build rebuilds and checks the original binaries, then builds the new cap against the public Metatype v0.1.1 libraries. The dispatcher is the verified v0.1.0 release ELF.

```sh
git clone --branch v0.1.1 https://github.com/a19q3/ckb-metatype.git ../ckb-metatype
rustup target add riscv64imac-unknown-none-elf
mkdir -p .local
tools/build-v0.1.1.sh > .local/contracts.json
diff -u evidence/contracts-v0.1.1.json .local/contracts.json
gh release download v0.1.0 --repo a19q3/ckb-metatype --pattern metatype --dir .local
python3 -m venv .local/venv
.local/venv/bin/pip install -r ../ckb-metatype/tools/requirements.txt
.local/venv/bin/python tools/baseline.py .local/baseline
PYTHONPATH=../ckb-metatype/tools .local/venv/bin/python tools/compare.py \
  --ckb /path/to/ckb --metatype ../ckb-metatype --dispatcher .local/metatype \
  --baseline .local/baseline > .local/comparison.json
```

The baseline helper makes read-only requests for the published A, F and Q ELFs and checks pinned SHA-256 digests. It supports `--rpc` for another archive-capable testnet endpoint and `--direct` when a proxy blocks the reads. The replay submits accepted transactions on a disposable loopback-only chain with fresh keys. Expected rejections use only the verifier. Cycles include secp256k1 verification and vary with fresh signatures.

[Raw v0.1.1 evidence](evidence/comparison-v0.1.1.json) includes actual committed transaction hashes, occupied capacity, verifier outcomes and attachment snapshots. [Contract hashes](evidence/contracts-v0.1.1.json) cover the rebuilt experiment code. To regenerate the report and chart, install `matplotlib==3.10.8` in a supported Python environment and run `python tools/report.py .local/comparison.json`.

## Sources and attribution

- Original QuotaCell revision `b3f488e47359361ea5d2549c152b8c0b418ebead`. Its implementation and original evidence are © 2026 Ticoworld under Apache-2.0. The replay obtains its deployed ELFs separately, subject to their upstream terms. [QuotaCell source](https://github.com/Ticoworld/QuotaCell/tree/b3f488e47359361ea5d2549c152b8c0b418ebead) · [Upstream dependency notices](https://github.com/Ticoworld/QuotaCell/blob/b3f488e47359361ea5d2549c152b8c0b418ebead/docs/DEPENDENCIES.md)
- Metatype dispatcher v0.1.0, revision `38bd969c3d374b1b1a77432b5de3a1d152af0368`. The current adapter uses the public v0.1.1 libraries. [Dispatcher release](https://github.com/a19q3/ckb-metatype/releases/tag/v0.1.0) · [Current SDK release](https://github.com/a19q3/ckb-metatype/releases/tag/v0.1.1)
- CKB 0.210.0, revision `119598b0a1146a569068ac80bdba7eb5f6292fd3`. [CKB source](https://github.com/nervosnetwork/ckb/tree/119598b0a1146a569068ac80bdba7eb5f6292fd3)
