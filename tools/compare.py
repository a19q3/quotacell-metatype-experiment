"""Compare native CKB Pocket composition profiles using only a disposable devnet for writes."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import devnet
from devnet import (Devnet, SigningKey, SECP256k1, CKB, FEE, auth_lock, binary,
                    ckb_hash, hx, le, metrics, namespace, occupied, output, pack_script,
                    point, script, script_hash, sign, transaction)
from smoke import adapter_table, fields, manifest
from baseline import BASELINE_SHA

ROOT = Path(__file__).resolve().parents[1]
TARGET = "target/riscv64imac-unknown-none-elf/release"
LEGACY_DISPATCHER_SHA = "d8d89d81e2e3955ad4aa7a9dc0a09fe86fcfe9fbc60bbda3601f333f16477176"
LEGACY_DISPATCHER_REVISION = "38bd969c3d374b1b1a77432b5de3a1d152af0368"
QUOTA_LIMIT = 183 * CKB
PAYMENT_REFS = b"P\x01\0\0\0\0\x01\0\x02\0"


def revision(directory):
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=directory, text=True).strip()


def common_secp_capacity(node):
    outputs, groups = node.call("get_block_by_number", "0x0")["transactions"]
    index = int(node.secp["out_point"]["index"], 16)
    group = groups["outputs"][index]
    data = binary(groups["outputs_data"][index])
    assert groups["hash"] == node.secp["out_point"]["tx_hash"] and len(data) == 76
    positions = []
    for outpoint in (data[4:40], data[40:]):
        assert hx(outpoint[:32]) == outputs["hash"]
        positions.append(int.from_bytes(outpoint[32:], "little"))
    cells = [(outputs["outputs"][i], binary(outputs["outputs_data"][i])) for i in positions]
    cells.append((group, data))
    return [occupied(cell["lock"], cell["type"], payload) // CKB for cell, payload in cells]


class Pocket:
    def __init__(self, node, variant, code, reference_source=0):
        self.node = node
        self.variant = variant
        self.composed = variant in ("C", "D", "E")
        self.cap_attached = variant == "D"
        self.reference_source = reference_source
        self.policy_shared = False
        self.keys = [SigningKey.generate(curve=SECP256k1) for _ in range(5)]
        self.locks = [auth_lock(key) for key in self.keys]
        self.owner, *self.delegates = self.locks[:3]
        self.recipients = self.locks[3:]
        self.actors = node.issue([(output(2_000 * CKB, lock), b"") for lock in self.locks[:3]])
        self.actor_capacity = [2_000 * CKB] * 3
        self.active_capacity = 600 * CKB
        self.retained = 134 * CKB if variant == "A" else 0
        self.policy = None
        self.payments = [0, 0]
        self.enabled = 3
        self.closed = False
        self.funds = []
        self.results = {}
        self.headers = []
        create_id = namespace(self.actors[0])
        self.extra = {}
        digest = {name: ckb_hash(binary) for name, binary in code.items()}
        if variant == "A":
            self.ty = script(hx(digest["Q"]), create_id)
            self.policy_lock = script(hx(digest["A"]), hash_type="data1")
            self.fund_lock = script(hx(digest["F"]), script_hash(self.ty), "data1")
        elif variant == "B":
            self.ty = script(hx(digest["quota-type"]), create_id)
            self.fund_lock = script(hx(digest["fund-lock"]), script_hash(self.ty) + script_hash(self.owner))
            self.policy_lock = self.fund_lock
        else:
            self.quota_rule = pack_script(script(hx(bytes([0x51]) * 32), hash_type="data"))
            entries = [(bytes([0x51]) * 32, 0, digest["quota-adapter"])]
            definitions = {"quota" if variant != "D" else "capped": ()}
            if variant in ("D", "E"):
                args = (bytes([reference_source]) if reference_source else b"") + le(61 * CKB, 8)
                self.cap_rule = pack_script(script(hx(bytes([0x52]) * 32), args, "data"))
                entries.append((bytes([0x52]) * 32, 0, digest["quotacell-payment-cap"]))
                definitions["capped"] = (self.cap_rule,)
            self.extra = {"table": adapter_table(entries)}
            self.definitions = {
                name: manifest([(rule, b"") for rule in (self.quota_rule, *rules)])
                for name, rules in definitions.items()
            }
            self.extra.update({f"definition_{key}": data for key, data in self.definitions.items()})
            self.ty = script(hx(digest["metatype"]), binary(node.genesis) + create_id + ckb_hash(self.extra["table"]))
            self.fund_lock = script(hx(digest["composed-fund-lock"]), script_hash(self.ty) + script_hash(self.owner))
            self.policy_lock = self.fund_lock
            self.headers = [node.genesis]
        self.code = code
        self.deps = node.deploy({**code, **self.extra})

    def state(self, payments=None, enabled=None):
        spent = self.payments if payments is None else payments
        flags = self.enabled if enabled is None else enabled
        if self.variant == "A":
            raw = b"QCN4\x04\x00\x00" + bytes([flags])
            raw += le(QUOTA_LIMIT, 16) + le(sum(spent), 16) + bytes(85)
            raw += self.keys[0].verifying_key.to_string("compressed")
            for delegate, recipient, used in zip(self.keys[1:3], self.recipients, spent, strict=True):
                raw += delegate.verifying_key.to_string("compressed") + pack_script(recipient)
                raw += le(QUOTA_LIMIT, 16) + le(used, 16) + le(62 * CKB, 16)
            return raw
        raw = script_hash(self.owner)
        for actor, recipient, used in zip(self.delegates, self.recipients, spent, strict=True):
            raw += script_hash(actor) + script_hash(recipient) + le(QUOTA_LIMIT, 8)
            raw += le(used, 8) + le(62 * CKB, 8)
        return raw + le(QUOTA_LIMIT, 8) + le(sum(spent), 8) + bytes([flags])

    def data(self, state, shared=False, attached=None, cap_state=b""):
        if not self.composed:
            return state
        attached = self.cap_attached if attached is None else attached
        entries = [(self.quota_rule, state)]
        if attached:
            entries.append((self.cap_rule, cap_state))
        if shared:
            definition = self.definitions["capped" if attached else "quota"]
            return b"\0" + ckb_hash(definition) + fields([state for _, state in sorted(entries)])
        return manifest(entries)

    def dependencies(self, *, shared_output=False, attached_output=None, without_policy=False):
        if without_policy:
            lock = {"A": "F", "B": "fund-lock"}.get(self.variant, "composed-fund-lock")
            return [self.node.secp, self.deps[lock]]
        attached_output = self.cap_attached if attached_output is None else attached_output
        required = []
        if self.policy_shared:
            required.append("definition_capped" if self.cap_attached else "definition_quota")
        if shared_output:
            required.append("definition_capped" if attached_output else "definition_quota")
        if self.cap_attached or attached_output:
            required.append("quotacell-payment-cap")
        return [self.node.secp, *(dep for name, dep in self.deps.items()
                                 if name in required or (not name.startswith("definition_")
                                    and name != "quotacell-payment-cap"))]

    def record(self, name, tx, failure=None, verify_only=False):
        if failure is not None:
            self.results[name] = {"rejected": self.node.reject(tx, failure)}
            return None
        self.results[name] = metrics(tx, self.node.verify(tx))
        for cell, data in zip(tx["outputs"], tx["outputs_data"], strict=True):
            if cell["type"] == self.ty:
                self.results[name]["policy_output_occupied_ckb"] = occupied(cell["lock"], self.ty, binary(data)) // CKB
                break
        if verify_only:
            return None
        tx_id = self.node.commit(tx)
        self.results[name]["tx_hash"] = tx_id
        return tx_id

    def fund(self, name, amounts, malformed=False):
        deposits = self.node.issue([
            (output(ckb * CKB, self.fund_lock), b"unsupported" if malformed else b"") for ckb in amounts
        ])
        self.funds.extend(zip(deposits, (ckb * CKB for ckb in amounts), strict=True))
        self.results[name] = {**self.node.last_issuance, "tx_hash": deposits[0]["tx_hash"]}

    def create(self, name="create", wrong_identity=False, failure=None):
        ty = self.ty
        if wrong_identity:
            args = binary(self.ty["args"])
            offset = 32 if self.composed else 0
            ty = script(self.ty["code_hash"], args[:offset] + bytes([9]) * 32 + args[offset + 32:])
        target = [(output(self.active_capacity, self.policy_lock, ty), self.data(self.state()))]
        balance = self.actor_capacity[0] - self.active_capacity - FEE
        target.append((output(balance, self.owner), b""))
        tx = transaction([self.actors[0]], [o for o, _ in target], [d for _, d in target],
                         self.dependencies(), self.headers)
        action = b"QCA1\0\xff\0\0" if self.variant == "A" else b""
        result = self.record(name, sign(tx, self.keys[0], [0], action), failure)
        if result:
            self.policy, self.actors[0] = point(result, 0), point(result, 1)
            self.actor_capacity[0] = balance

    def transition(self, name, operation, actor=0, *, amount=61, shared_out=False,
                   attach=False, negative=None, failure=None, verify_only=False, trace_intent=False):
        policy, account = self.policy, self.actors[actor]
        inputs = [policy, account]
        balance = self.actor_capacity[actor] - FEE
        active = self.active_capacity if not self.closed else self.retained
        next_enabled = self.enabled
        next_payments = self.payments.copy()
        payout = amount * CKB
        if operation in ("pay", "close", "sweep"):
            inputs += [fund for fund, _ in self.funds]
        total_funds = sum(value for _, value in self.funds)
        actor_lock = self.locks[actor]
        payee = self.recipients[actor - 1] if actor else self.owner
        action = b""
        change = [(output(balance, actor_lock), b"")]
        next_cap = (self.cap_attached or attach) and negative != "drop_rule"
        references = PAYMENT_REFS
        if negative == "wrong_references":
            references = PAYMENT_REFS[:-2] + le(3, 2)
        elif negative == "reordered_outputs":
            references = PAYMENT_REFS[:-4] + le(2, 2) + le(1, 2)
        cap_state = references if next_cap and self.reference_source == 1 and operation == "pay" else b""
        if operation == "pay":
            if negative == "wrong_recipient":
                payee = self.owner
            if negative != "reset_counter":
                next_payments[actor - 1] += payout
            action = b"QCA1" + bytes([1, actor - 1, 0, 0])
        elif operation == "revoke":
            next_enabled = self.enabled & ~1
            action = b"QCA1\x03\xff\0\0\x01"
        elif operation in ("close", "sweep"):
            action = b"QCA1" + bytes([4 if operation == "close" else 5, 255, 0, 0])

        state = self.state(next_payments, next_enabled)
        if self.variant == "A" and operation in ("close", "sweep"):
            state = b"QCT4\x04\x00\x01\0" + binary(self.owner["args"])
        next_policy = [(output(active, self.policy_lock, self.ty),
                        self.data(state, shared_out, next_cap, cap_state))]
        if operation == "pay":
            if negative == "duplicate":
                next_policy.append(next_policy[0])
                change = [(output(balance - active, actor_lock), b"")]
            target = [*next_policy, (output(total_funds - payout, self.fund_lock), b""),
                      (output(payout, payee), b""), *change]
            if negative == "reordered_outputs":
                target[1], target[2] = target[2], target[1]
        elif operation in ("close", "sweep"):
            if self.variant == "A":
                target = [(output(self.retained, self.policy_lock, self.ty), state)]
                if total_funds:
                    target.append((output(total_funds, self.owner), b""))
                target.append((output(balance + active - self.retained, actor_lock), b""))
            else:
                target = [(output(balance + active + total_funds, self.owner), b"")]
        else:
            target = [*next_policy, *change]
        tx = transaction(inputs, [o for o, _ in target], [d for _, d in target],
                         self.dependencies(shared_output=shared_out, attached_output=next_cap), self.headers)
        if next_cap and self.reference_source == 2 and operation == "pay" and negative != "missing_references":
            group = ckb_hash(self.cap_rule)
            tx["witnesses"].append(hx(b"CKB Metatype v1 Witness\0\x01" +
                                      script_hash(self.ty) + group + le(1, 2) +
                                      fields([references, b"", b""])))
        sign(tx, self.keys[actor], [1], action if self.variant == "A" else b"")
        if negative == "replay_signature":
            tx["witnesses"][1] = self.first_delegate_witness
        elif name == "delegate_1":
            self.first_delegate_witness = tx["witnesses"][1]
        result = self.record(name, tx, failure, verify_only)
        if trace_intent:
            self.results[name]["payment_intent"] = {
                "actor": hx(script_hash(actor_lock)), "recipient": hx(script_hash(payee)),
                "amount_shannons": payout, "fund_inputs": [fund for fund, _ in self.funds],
                "quota_successor_hash": hx(ckb_hash(state)),
            }
        if not result:
            return
        self.enabled, self.payments = next_enabled, next_payments
        self.cap_attached = next_cap
        self.policy_shared = shared_out
        actor_last = len(target) - 1
        self.actors[actor] = point(result, actor_last)
        self.actor_capacity[actor] = int(target[actor_last][0]["capacity"], 16)
        if operation == "pay":
            self.funds = [(point(result, 1), total_funds - payout)]
        elif operation in ("close", "sweep"):
            self.closed, self.funds = True, []
        self.policy = point(result, 0) if not self.closed or self.variant == "A" else None

    def recover_independently(self, name, actor, failure=None):
        total = sum(value for _, value in self.funds)
        balance = total + self.actor_capacity[actor] - FEE
        # Deliberately put the actor at index 0. It is an ordinary secp Lock Cell.
        tx = transaction([self.actors[actor], *(fund for fund, _ in self.funds)],
                         [output(balance, self.locks[actor])], [b""], self.dependencies(without_policy=True))
        result = self.record(name, sign(tx, self.keys[actor], [0]), failure)
        if result:
            self.actors[actor], self.actor_capacity[actor] = point(result, 0), balance
            self.funds = []

    def snapshot(self):
        live = self.node.call("get_live_cell", self.policy, True)
        assert live["status"] == "live"
        cell = live["cell"]
        assert cell["output"]["type"] == self.ty and cell["output"]["lock"] == self.policy_lock
        raw = binary(cell["data"]["content"])
        assert raw == self.data(self.state(), self.policy_shared)
        return {
            "policy_outpoint": self.policy,
            "physical_type_hash": hx(script_hash(cell["output"]["type"])),
            "fund_lock_hash": hx(script_hash(cell["output"]["lock"])),
            "pocket_namespace": hx(binary(self.ty["args"])[32:64]),
            "adapter_table_hash": hx(binary(self.ty["args"])[64:96]),
            "attached_constraints": ["quota", "cap"] if self.cap_attached else ["quota"],
            "quota_state_hash": hx(ckb_hash(self.state())),
            "shared_used_ckb": sum(self.payments) // CKB,
            "individual_used_ckb": [used // CKB for used in self.payments],
            "enabled_mask": self.enabled,
            "manifest_data_hash": hx(ckb_hash(raw)),
            "occupied_ckb": occupied(self.policy_lock, self.ty, raw) // CKB,
        }

    def attach_cap(self):
        self.transition("within_cap_before_attachment", "pay", 1, verify_only=True, trace_intent=True)
        self.transition("above_cap_before_attachment", "pay", 1, amount=62, verify_only=True, trace_intent=True)
        self.transition("delegate_attachment_rejected", "pay", 1, attach=True, failure="error code 46")
        self.transition("attachment_with_quota_change_rejected", "revoke", attach=True, failure="error code 46")
        before = self.snapshot()
        self.transition("attach_cap", "attach", attach=True)
        after = self.snapshot()
        stable = ("physical_type_hash", "fund_lock_hash", "pocket_namespace", "adapter_table_hash", "quota_state_hash",
                  "shared_used_ckb", "individual_used_ckb", "enabled_mask")
        assert all(before[key] == after[key] for key in stable)
        assert before["policy_outpoint"] != after["policy_outpoint"]
        assert before["manifest_data_hash"] != after["manifest_data_hash"]
        self.transition("above_cap_after_attachment", "pay", 1, amount=62, failure="error code 46", trace_intent=True)
        assert (self.results["above_cap_before_attachment"]["payment_intent"] ==
                self.results["above_cap_after_attachment"]["payment_intent"])
        self.attachment = {"before": before, "after": after}

    def run(self):
        self.create("policy_identity_substitution", wrong_identity=True, failure="ValidationFailure")
        self.create()
        self.fund("deposit", [256])
        self.recover_independently("wrong_owner", 1, "ValidationFailure")
        self.transition("wrong_recipient", "pay", 1, negative="wrong_recipient", failure="ValidationFailure")
        self.transition("duplicate_policy", "pay", 1, negative="duplicate", failure="ValidationFailure")
        self.transition("counter_reset_rejected", "pay", 1, negative="reset_counter", failure="ValidationFailure")
        if self.cap_attached:
            self.transition("cap_enforced_at_issuance", "pay", 1, amount=62, failure="error code 46")
        if self.composed:
            self.transition("delegate_1_shared_verifier", "pay", 1, shared_out=True, verify_only=True)
            self.transition("wrong_recipient_shared", "pay", 1, shared_out=True,
                            negative="wrong_recipient", failure="ValidationFailure")
        self.transition("delegate_1", "pay", 1)
        self.transition("replayed_authorisation", "pay", 1, negative="replay_signature", failure="ValidationFailure")
        if self.variant == "E":
            self.attach_cap()
        if self.cap_attached:
            self.transition("remove_cap_rejected", "pay", 1, negative="drop_rule", failure="error code 46")
        if self.reference_source:
            self.transition("incorrect_reference", "pay", 1, negative="wrong_references", failure="error code 46")
            self.transition("reordered_outputs", "pay", 1, negative="reordered_outputs", failure="error code 46")
        if self.reference_source == 2:
            self.transition("missing_group_reference", "pay", 1, negative="missing_references", failure="error code 46")

        assert QUOTA_LIMIT - sum(self.payments) >= 61 * CKB
        assert QUOTA_LIMIT - self.payments[0] >= 61 * CKB
        self.transition("revocation_control", "pay", 1, verify_only=True, trace_intent=True)
        if self.variant == "E":
            assert (self.results["within_cap_before_attachment"]["payment_intent"] ==
                    self.results["revocation_control"]["payment_intent"])
        before_revocation = self.payments.copy()
        self.transition("owner_revocation", "revoke", shared_out=self.composed)
        assert self.payments == before_revocation and self.enabled == 2
        code = {"A": 17, "B": 52}.get(self.variant, 46)
        self.transition("revoked_delegate", "pay", 1, failure=f"error code {code}", trace_intent=True)
        before, after = (self.results[key]["payment_intent"] for key in ("revocation_control", "revoked_delegate"))
        assert all(before[key] == after[key] for key in ("actor", "recipient", "amount_shannons", "fund_inputs"))

        self.fund("replenishment", [110])
        self.transition("delegate_2_multi_fund", "pay", 2)
        self.transition("delegate_2_exhaust_allowance", "pay", 2)
        assert sum(self.payments) == QUOTA_LIMIT and self.payments[1] + 61 * CKB <= QUOTA_LIMIT
        self.transition("shared_exhausted", "pay", 2, failure="ValidationFailure")
        self.transition("unauthorised_retirement", "close", 1, failure="ValidationFailure")
        if self.variant == "A":
            self.recover_independently("active_owner_independent_recovery", 0, "ValidationFailure")
        else:
            self.recover_independently("active_owner_independent_recovery", 0)
            self.fund("deposit_for_close", [183])
        self.transition("close", "close")
        self.fund("late_deposit", [105])
        if self.variant == "A":
            self.recover_independently("delegate_after_close", 1, "ValidationFailure")
            self.transition("late_owner_recovery", "sweep")
        else:
            self.recover_independently("delegate_after_close", 1, "ValidationFailure")
            self.recover_independently("late_owner_recovery", 0)
            self.create("namespace_reuse", failure="ValidationFailure")
            self.fund("malformed_late_deposit", [150], malformed=True)
            self.recover_independently("malformed_native_recovery", 0)
        shared_occupied = (occupied(self.policy_lock, self.ty, self.data(self.state(), True)) // CKB
                           if self.composed else None)
        summary = {
            "capacity_ckb": {
                "active_occupied": occupied(self.policy_lock, self.ty, self.data(self.state())) // CKB,
                "active_shared_occupied": shared_occupied,
                "active_funded": self.active_capacity // CKB,
                "active_initial_occupied": occupied(
                    self.policy_lock, self.ty, self.data(self.state(), attached=self.variant == "D")
                ) // CKB,
                "retained_after_closure": self.retained // CKB,
                "fund_cell_occupied": occupied(self.fund_lock) // CKB,
                "custom_code_and_table_cells": {
                    name: occupied(self.node.faucet, data=raw) // CKB for name, raw in {**self.code, **self.extra}.items()
                },
            },
            "code_hashes": {
                name: {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
                       "ckb_data_hash": hx(ckb_hash(raw))}
                for name, raw in self.code.items()
            },
            "tests": self.results,
        }
        if self.variant == "E":
            summary["attachment"] = self.attachment
        return summary


def interface_sample(result):
    cases = result["tests"]
    return {
        "payment": cases["delegate_1"],
        "checks": {key: value for key, value in cases.items()
                   if key in ("incorrect_reference", "missing_group_reference", "reordered_outputs")},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ckb", required=True)
    parser.add_argument("--metatype", required=True, type=Path)
    parser.add_argument("--dispatcher", required=True, type=Path, help="Verified Metatype v0.1.0 dispatcher ELF")
    parser.add_argument("--baseline", required=True, type=Path, help="Directory containing verified Q, A and F binaries")
    args = parser.parse_args()
    if Path(devnet.__file__).resolve().parent != (args.metatype / "tools").resolve():
        parser.error("Set PYTHONPATH to the tools directory in the same Metatype checkout")
    a = {name: (args.baseline / name).read_bytes() for name in BASELINE_SHA}
    if any(hashlib.sha256(raw).hexdigest() != BASELINE_SHA[name] for name, raw in a.items()):
        parser.error("Baseline requires the exact public A, F and Q artifacts")
    contracts = ROOT / TARGET
    b = {name: (contracts / name).read_bytes() for name in ("fund-lock", "quota-type")}
    c = {name: (contracts / name).read_bytes() for name in ("composed-fund-lock", "quota-adapter")}
    c["metatype"] = args.dispatcher.read_bytes()
    if hashlib.sha256(c["metatype"]).hexdigest() != LEGACY_DISPATCHER_SHA:
        parser.error("The comparison requires the unchanged v0.1.0 dispatcher")
    legacy_quota = json.loads((ROOT / "evidence/contracts.json").read_text())["quota-adapter"]["sha256"]
    if hashlib.sha256(c["quota-adapter"]).hexdigest() != legacy_quota:
        parser.error("The quota adapter must match the original v0.1.0 release")
    cap = (contracts / "quotacell-payment-cap").read_bytes()
    composed = {**c, "quotacell-payment-cap": cap}
    with Devnet(args.ckb) as node:
        result = {
            "ckb_version": subprocess.check_output([args.ckb, "--version"], text=True).strip(),
            "ckb_sha256": hashlib.sha256(Path(args.ckb).read_bytes()).hexdigest(),
            "metatype_tools_revision": revision(args.metatype),
            "dispatcher_revision": LEGACY_DISPATCHER_REVISION,
            "original_quota_sha256": legacy_quota,
            "quotacell_revision": "b3f488e47359361ea5d2549c152b8c0b418ebead",
            "baseline_sha256": BASELINE_SHA,
            "common_genesis_secp_cell_occupied_ckb": common_secp_capacity(node),
            "variants": {},
        }
        for key, contract_set in [("A", a), ("B", b), ("C", c), ("D", composed), ("E", composed)]:
            print(f"Testing {key}", file=sys.stderr, flush=True)
            result["variants"][key] = Pocket(node, key, contract_set).run()
        result["interfaces"] = {"fixed": interface_sample(result["variants"]["D"])}
        for label, source in (("semantic_state", 1), ("group_message", 2)):
            print(f"Testing {label}", file=sys.stderr, flush=True)
            result["interfaces"][label] = interface_sample(Pocket(node, "D", composed, source).run())
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
