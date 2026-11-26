"""Render the v0.1.1 report and charts from raw verifier evidence."""

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import StrMethodFormatter

PROFILES = ("A", "B", "C", "D", "E")
COLOURS = ("#4b5563", "#225ea8", "#087f6d", "#a04e21", "#723c93")
LIFECYCLE = (
    ("create", "Create"), ("delegate_1", "First payment"), ("attach_cap", "Attach cap"),
    ("owner_revocation", "Revoke"), ("delegate_2_multi_fund", "Payment (two funds)"),
    ("close", "Close"), ("late_owner_recovery", "Late recovery"),
)
CAPACITY = (
    ("active_initial_occupied", "Policy at issuance"), ("active_occupied", "Active policy, inline"),
    ("active_shared_occupied", "Active policy, shared form"), ("retained_after_closure", "Policy after closure"),
    ("fund_cell_occupied", "Each protected fund"),
)
METRICS = ("tx_bytes", "witness_bytes", "cycles", "cell_deps", "header_deps")


def case_metric(variant, step, metric):
    return variant["tests"].get(step, {}).get(metric)


def chart(data, destination):
    figure, axes = plt.subplots(1, 3, figsize=(15, 5.7), gridspec_kw={"width_ratios": [0.92, 1, 1]})
    for ax, title, xlabel, rows, getter in (
        (axes[0], "Lifecycle capacity", "Occupied capacity (CKB)", CAPACITY,
         lambda variant, key: variant["capacity_ckb"][key]),
        (axes[1], "Transaction size", "Molecule transaction bytes", LIFECYCLE,
         lambda variant, key: case_metric(variant, key, "tx_bytes")),
        (axes[2], "RISC-V verification", "Cycles (millions)", LIFECYCLE,
         lambda variant, key: (case_metric(variant, key, "cycles") or 0) / 1e6),
    ):
        for index, (variant, colour) in enumerate(zip(PROFILES, COLOURS, strict=True)):
            xs = [getter(data[variant], key) or 0 for key, _ in rows]
            ys = [row + (index - 2) * 0.16 for row in range(len(rows))]
            ax.barh(ys, xs, height=0.14, color=colour, label=variant, zorder=2)
        ax.set_yticks(range(len(rows)), [label for _, label in rows], fontsize=8)
        ax.invert_yaxis()
        ax.set(title=title, xlabel=xlabel)
        ax.spines[["top", "right", "left"]].set_visible(False)
        ax.tick_params(axis="y", length=0, pad=5)
        ax.xaxis.set_major_formatter(StrMethodFormatter("{x:,.0f}" if ax != axes[2] else "{x:,.1f}"))
        ax.grid(axis="x", alpha=0.24, zorder=1)
    handles, labels = axes[0].get_legend_handles_labels()
    figure.legend(handles, labels, loc="lower center", ncol=5, frameon=False)
    figure.subplots_adjust(left=0.15, right=0.99, bottom=0.16, top=0.91, wspace=0.61)
    figure.savefig(destination, dpi=160)
    plt.close(figure)


def report(run):
    data = run["variants"]
    e = data["E"]
    before, after = e["attachment"]["before"], e["attachment"]["after"]

    def capacity_row(key, title):
        values = [data[p]["capacity_ckb"][key] for p in PROFILES]
        return f"| {title} | " + " | ".join("—" if value is None else f"{value:,}" for value in values) + " |"

    def measurement(row):
        return " / ".join(f"{row[field]:,}" for field in METRICS)

    same_payment_before, same_payment_after = (
        e["tests"][key] for key in ("within_cap_before_attachment", "revocation_control")
    )
    cap_deployment = data["D"]["capacity_ckb"]["custom_code_and_table_cells"]
    total_costs = [sum(data[profile]["capacity_ckb"]["custom_code_and_table_cells"].values())
                   for profile in PROFILES]
    parts = [
        "# Results v0.1.1",
        "",
        "Profile E attached the cap on an existing Pocket after its first delegated payment, then completed further delegated payments and owner closure. A matched 62 CKB payment passed the verifier before attachment and failed afterwards. The same 61 CKB payment passed on both sides of attachment as a control.",
        "",
        f"The committed successor kept the physical Type hash, namespace, fund Lock, table hash and all quota state bytes. Shared usage stayed at {after['shared_used_ckb']} CKB and delegate usage at {after['individual_used_ckb']} CKB. The policy outpoint and Manifest data hash changed. The Quota ELF remained byte for byte identical to the historical release.",
        "",
        "![Occupied capacity, transaction sizes and verifier cycles for profiles A–E](evidence/comparison-v0.1.1.png)",
        "",
        "## Occupied capacity",
        "",
        "| CKB | A | B | C | D | E |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
        *(capacity_row(key, title) for key, title in CAPACITY),
        "| Shared custom ELFs, table and definitions | " + " | ".join(f"{cost:,}" for cost in total_costs) + " |",
        "",
        f"Attachment grew the same inline policy from {before['occupied_ckb']} to {after['occupied_ckb']} CKB occupied, an increase of {after['occupied_ckb'] - before['occupied_ckb']} CKB. In this run every active policy held 600 CKB. Shared deployment costs include a {cap_deployment['quotacell-payment-cap']:,} CKB code Cell for the new cap. D and E already deploy that code. E also stores the optional Quota-only shared definition. Common genesis secp code, data and dep group occupy {sum(run['common_genesis_secp_cell_occupied_ckb']):,} CKB and are omitted from custom deployment totals.",
        "",
        "## Transactions",
        "",
        "Each entry lists transaction bytes / witness payload bytes / verification cycles / Cell Deps / Header Deps. One secp dep-group entry resolves to its code and data Cells. All profiles use the same 183 CKB shared and individual limits, a 62 CKB quota ceiling per payment, and 61 CKB in each committed payment. D applies an additional 61 CKB cap from issuance and E applies it after attachment. C, D and E separately exercise both inline and shared encodings.",
        "",
        "| Scenario | A | B | C | D | E |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for key, title in LIFECYCLE:
        row = [measurement(value) if (value := data[p]["tests"].get(key)) else "—" for p in PROFILES]
        parts.append(f"| {title} | {' | '.join(row)} |")
    parts.extend([
        "",
        f"Profile E's paired 61 CKB dry runs measured {measurement(same_payment_before)} before attachment and {measurement(same_payment_after)} afterwards. The continuing cap added {same_payment_after['tx_bytes'] - same_payment_before['tx_bytes']} transaction bytes, {same_payment_after['cell_deps'] - same_payment_before['cell_deps']} Cell Dep and {same_payment_after['cycles'] - same_payment_before['cycles']:,} measured cycles. The witness payload stayed at {same_payment_after['witness_bytes']} bytes. Signatures differ between transactions and contribute to cycle variation.",
        "",
        "## Payment interface variants",
        "",
        "| Approach | Payment metrics in the order above | Occupied policy successor (CKB) |",
        "| --- | ---: | ---: |",
    ])
    for key, title in (("fixed", "Fixed positions"), ("semantic_state", "Successor-state references"),
                       ("group_message", "Group Message references")):
        payment = run["interfaces"][key]["payment"]
        parts.append(f"| {title} | {measurement(payment)} | {payment['policy_output_occupied_ckb']} |")
    parts.extend([
        "",
        "The two reference carriers rejected a claim using the actor's change as the payment. The witness mode also rejected an absent Group Message. Reordering outputs and providing matching references still failed the combined policy. The cap checked actual transaction roles in both modes, and the original quota validator retained its fixed positions. [Wire layout and responsibilities](PAYMENT-INTERFACE.md)",
        "",
        "## Verification and limits",
        "",
    ])
    for label in PROFILES:
        cases = data[label]["tests"].values()
        committed = sum("tx_hash" in case for case in cases)
        rejected = sum("rejected" in case for case in cases)
        verified = len(cases) - committed - rejected
        dry_runs = "dry run" if verified == 1 else "dry runs"
        parts.append(f"- **{label}** passed {committed} committed cases, {verified} positive {dry_runs} and {rejected} expected rejections.")
    parts.extend([
        "",
        "Each profile verifies the same delegate payment before and after revocation while sufficient shared and individual allowance remains. Shared exhaustion is a separate case using the still-enabled delegate. E additionally rejects attachment by a delegate, attachment with a quota-state change and removal while the policy survives. B–E recover late funds with independent owner authority and zero retained policy Capacity. Independent owner recovery permits withdrawal during the active phase.",
        "",
        "The single quota Cell still serialises delegated spending. Attachment depends on an existing immutable table mapping. The additional cap is scoped to native CKB. The reference variants do not change Quota's position rules. The experiment exercises an isolated devnet and is unaudited.",
        "",
        "See [raw measurements and exact hashes](evidence/comparison-v0.1.1.json), [build hashes](evidence/contracts-v0.1.1.json), [reproduction commands](README.md) and [historical measurements](RESULTS.md).",
    ])
    return "\n".join(parts) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("raw", type=Path)
    args = parser.parse_args()
    run = json.loads(args.raw.read_text())
    root = Path(__file__).resolve().parents[1]
    chart(run["variants"], root / "evidence/comparison-v0.1.1.png")
    (root / "RESULTS-v0.1.1.md").write_text(report(run))


if __name__ == "__main__":
    main()
