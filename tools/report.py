"""Render the comparison's table and charts from raw verifier evidence."""

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import StrMethodFormatter

VARIANTS = ("A", "B", "C", "C+cap")
COLOURS = ("#4b5563", "#225ea8", "#087f6d", "#a04e21")
LIFECYCLE = (
    ("create", "Create"), ("deposit", "Deposit"), ("delegate_1", "Delegate 1"),
    ("delegate_2_multi_fund", "Delegate 2 (2 funds)"), ("owner_revocation", "Revoke"),
    ("close", "Close"), ("late_owner_recovery", "Late recovery"),
)
CAPACITY = (
    ("active_occupied", "Active policy (inline)"), ("active_shared_occupied", "Active policy (shared where used)"),
    ("retained_after_closure", "Policy after closure"), ("fund_cell_occupied", "Each protected fund"),
)


def chart(data, destination):
    figure, axes = plt.subplots(1, 3, figsize=(15, 5.6), gridspec_kw={"width_ratios": [0.92, 1, 1]})
    for ax, title, xlabel, rows, getter in (
        (axes[0], "Lifecycle capacity", "Occupied capacity (CKB)", CAPACITY,
         lambda variant, key: variant["capacity_ckb"][key]),
        (axes[1], "Transaction size", "Molecule transaction bytes", LIFECYCLE,
         lambda variant, key: variant["tests"][key]["tx_bytes"]),
        (axes[2], "RISC-V verification", "Cycles (millions)", LIFECYCLE,
         lambda variant, key: variant["tests"][key]["cycles"] / 1e6),
    ):
        for index, (variant, colour) in enumerate(zip(VARIANTS, COLOURS, strict=True)):
            xs = [getter(data[variant], key) for key, _ in rows]
            ys = [row + (index - 1.5) * 0.19 for row in range(len(rows))]
            ax.barh(ys, xs, height=0.17, color=colour, label=variant, zorder=2)
        ax.set_yticks(range(len(rows)), [label for _, label in rows], fontsize=8)
        ax.invert_yaxis()
        ax.set(title=title, xlabel=xlabel)
        ax.spines[["top", "right", "left"]].set_visible(False)
        ax.tick_params(axis="y", length=0, pad=5)
        ax.xaxis.set_major_formatter(StrMethodFormatter("{x:,.0f}" if ax != axes[2] else "{x:,.1f}"))
        ax.grid(axis="x", alpha=0.24, zorder=1)
    handles, labels = axes[0].get_legend_handles_labels()
    figure.legend(handles, labels, loc="lower center", ncol=4, frameon=False)
    figure.subplots_adjust(left=0.143, right=0.99, bottom=0.16, top=0.91, wspace=0.63)
    figure.savefig(destination, dpi=160)
    plt.close(figure)


def report(run):
    data = run["variants"]

    def values(getter):
        return " | ".join(f"{getter(data[key]):,}" for key in VARIANTS)

    parts = [
        "# Results",
        "",
        "Both independent-recovery profiles released the entire policy Cell and recovered a late deposit. Their owner can also withdraw during the active phase. The original QCN4/QCT4 lifecycle retained 134 CKB and required the live terminal policy for late recovery.",
        "",
        "The conventional profile B used fewer bytes, dependencies and cycles for delegated payments than C. Both serialise delegated spends through one policy Cell. C's benefit in this experiment was a separate payment-cap adapter that enforced a tighter ceiling and rejected its own removal while the quota policy was live. Its introduction left the quota adapter byte-for-byte unchanged.",
        "",
        "![Comparison of occupied capacity, transaction size and verifier cycles](evidence/comparison.png)",
        "",
        "## Capacity",
        "",
        "| CKB | A | B | C | C+cap |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for key, title in CAPACITY:
        parts.append(f"| {title} | {values(lambda variant: variant['capacity_ckb'][key])} |")
    parts.append(f"| Funded active policy | {values(lambda variant: variant['capacity_ckb']['active_funded'])} |")
    parts.append(f"| Shared custom code, table and definition | {values(lambda variant: sum(variant['capacity_ckb']['custom_code_and_table_cells'].values()))} |")
    common = run["common_genesis_secp_cell_occupied_ckb"]
    parts.extend([
        "",
        f"Each active policy was funded at 600 CKB. Closure released 466 CKB in A and 600 CKB in B and C before fees. Custom deployment costs are shared across Pockets using the same code and definitions. The optional shared-definition Cell is included for C and C+cap. Verification cycles cover the whole signed transaction. The genesis secp code, precomputation data and dep-group Cells together occupy {sum(common):,} CKB, a common chain cost excluded from the custom deployment totals.",
        "",
        "## Transactions",
        "",
        "Each entry gives transaction bytes / witness payload bytes / verification cycles / Cell Deps / Header Deps. Dep counts are encoded transaction entries. The shared secp dep-group entry resolves to two code/data Cells. C uses an inline input and shared successor for its first payment, and the reverse for its second.",
        "",
        "| Scenario | A | B | C | C+cap |",
        "| --- | ---: | ---: | ---: | ---: |",
    ])
    for key, title in LIFECYCLE:
        entries = []
        for variant in data.values():
            metrics = variant["tests"][key]
            entries.append(" / ".join(f"{metrics[field]:,}" for field in
                                      ("tx_bytes", "witness_bytes", "cycles", "cell_deps", "header_deps")))
        parts.append(f"| {title} | {' | '.join(entries)} |")
    parts.extend(["", "## Verification", ""])
    for label in VARIANTS:
        cases = data[label]["tests"].values()
        committed = sum("tx_hash" in case for case in cases)
        rejected = sum("rejected" in case for case in cases)
        verified = len(cases) - committed - rejected
        dry_runs = f", plus {verified} accepted dry run" if verified else ""
        parts.append(f"- **{label}** passed {committed} committed cases and {rejected} expected rejections{dry_runs}.")
    parts.extend([
        "",
        "Cases cover authenticated creation, both delegate payments, exhausted shared allowance after treasury replenishment, incorrect recipients, revocation, and aggregation of two protected fund inputs. They also cover policy duplication, identity substitution, counter reset, a stale authorisation, unauthorised retirement, owner closure and late recovery. B and C reject reclaiming delegated authority after retirement and recover malformed untyped native deposits via the owner path. In C the verifier accepts both inline and shared payment successors from the same state and rejects an incorrect recipient with either representation.",
        "",
        "The retained-capacity result depends on independent owner authority. Strict recovery-after-closure under a zero-retained-capacity construction remains open. Measured cycles include secp verification and can vary with fresh signatures. Maximum fan-in, general asset recovery and reorganisations were outside this run.",
        "",
        "See [raw results and exact deployed hashes](evidence/comparison.json), [first-party builds](evidence/contracts.json), [failed attempts and corrections](evidence/run-notes.md), and [reproduction commands](README.md).",
    ])
    return "\n".join(parts) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("raw", type=Path)
    args = parser.parse_args()
    run = json.loads(args.raw.read_text())
    root = Path(__file__).resolve().parents[1]
    chart(run["variants"], root / "evidence/comparison.png")
    (root / "RESULTS.md").write_text(report(run))


if __name__ == "__main__":
    main()
