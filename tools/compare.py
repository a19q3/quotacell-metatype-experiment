"""Compare three native-CKB Pocket profiles using only a disposable devnet for writes."""

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
    def __init__(self, node, variant, code, term_cap=None):
        self.node = node
        self.variant = variant
        self.term_cap = term_cap
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
            self.rules = [pack_script(script(hx(bytes([0x51]) * 32), hash_type="data"))]
            entries = [(bytes([0x51]) * 32, 0, digest["quota-adapter"])]
            if term_cap:
                self.rules.append(pack_script(script(hx(bytes([0x52]) * 32), le(term_cap * CKB, 8), "data")))
                entries.append((bytes([0x52]) * 32, 0, digest["payment-cap"]))
            self.rules.sort()
            self.definition = manifest([(rule, b"") for rule in self.rules])
            self.extra = {"table": adapter_table(entries), "definition": self.definition}
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
            raw += le(122 * CKB, 16) + le(sum(spent), 16) + bytes(85)
            raw += self.keys[0].verifying_key.to_string("compressed")
            for delegate, recipient, used in zip(self.keys[1:3], self.recipients, spent, strict=True):
                raw += delegate.verifying_key.to_string("compressed") + pack_script(recipient)
                raw += le(122 * CKB, 16) + le(used, 16) + le(61 * CKB, 16)
            return raw
        raw = script_hash(self.owner)
        for actor, recipient, used in zip(self.delegates, self.recipients, spent, strict=True):
            raw += script_hash(actor) + script_hash(recipient) + le(122 * CKB, 8)
            raw += le(used, 8) + le((62 if self.term_cap else 61) * CKB, 8)
        return raw + le(122 * CKB, 8) + le(sum(spent), 8) + bytes([flags])

    def data(self, state, shared=False, omit_last_rule=False):
        if self.variant != "C":
            return state
        states = [state, b""] if self.term_cap else [state]
        if shared:
            return b"\0" + ckb_hash(self.definition) + fields(states)
        entries = list(zip(self.rules, states, strict=True))
        return manifest(entries[:-1] if omit_last_rule else entries)

    def dependencies(self, shared=False, without_policy=False):
        if without_policy:
            lock = {"A": "F", "B": "fund-lock", "C": "composed-fund-lock"}[self.variant]
            return [self.node.secp, self.deps[lock]]
        return [self.node.secp, *(d for name, d in self.deps.items() if name != "definition" or shared)]

    def record(self, name, tx, failure=None, verify_only=False):
        if failure is not None:
            self.results[name] = {"rejected": self.node.reject(tx, failure)}
            return None
        self.results[name] = metrics(tx, self.node.verify(tx))
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
            offset = 32 if self.variant == "C" else 0
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
                   shared_in=False, negative=None, failure=None, verify_only=False):
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
                        self.data(state, shared_out, negative == "drop_rule"))]
        if operation == "pay":
            if negative == "duplicate":
                next_policy.append(next_policy[0])
                change = [(output(balance - active, actor_lock), b"")]
            target = [*next_policy, (output(total_funds - payout, self.fund_lock), b""),
                      (output(payout, payee), b""), *change]
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
                         self.dependencies(shared_out or shared_in), self.headers)
        sign(tx, self.keys[actor], [1], action if self.variant == "A" else b"")
        if negative == "replay_signature":
            tx["witnesses"][1] = self.first_delegate_witness
        elif name == "delegate_1":
            self.first_delegate_witness = tx["witnesses"][1]
        result = self.record(name, tx, failure, verify_only)
        if not result:
            return
        self.enabled, self.payments = next_enabled, next_payments
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

    def run(self):
        self.create("policy_identity_substitution", wrong_identity=True, failure="ValidationFailure")
        self.create()
        self.fund("deposit", [256])
        self.recover_independently("wrong_owner", 1, "ValidationFailure")
        self.transition("wrong_recipient", "pay", 1, negative="wrong_recipient", failure="ValidationFailure")
        self.transition("duplicate_policy", "pay", 1, negative="duplicate", failure="ValidationFailure")
        self.transition("malicious_counter_reset", "pay", 1, negative="reset_counter", failure="ValidationFailure")
        if self.term_cap:
            self.transition("extra_cap_enforced", "pay", 1, amount=62, failure="ValidationFailure")
            self.transition("extra_cap_required", "pay", 1, negative="drop_rule", failure="ValidationFailure")
        if self.variant == "C":
            self.transition("delegate_1_inline_verifier", "pay", 1, verify_only=True)
            self.transition("wrong_recipient_shared", "pay", 1, shared_out=True,
                            negative="wrong_recipient", failure="ValidationFailure")
        self.transition("delegate_1", "pay", 1, shared_out=self.variant == "C")
        self.transition("replayed_authorisation", "pay", 1, negative="replay_signature",
                        shared_in=self.variant == "C", failure="ValidationFailure")
        self.fund("replenishment", [110])
        self.transition("delegate_2_multi_fund", "pay", 2, shared_in=self.variant == "C")
        self.transition("shared_exhausted", "pay", 1, failure="ValidationFailure")
        self.transition("owner_revocation", "revoke")
        self.transition("revoked_delegate", "pay", 1, failure="ValidationFailure")
        self.transition("unauthorised_retirement", "close", 1, failure="ValidationFailure")
        if self.variant == "A":
            self.recover_independently("active_owner_independent_recovery", 0, "ValidationFailure")
        else:
            self.recover_independently("active_owner_independent_recovery", 0)
            self.fund("deposit_for_close", [244])
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
        shared = self.data(self.state(), True) if self.variant == "C" else self.state()
        return {
            "capacity_ckb": {
                "active_occupied": occupied(self.policy_lock, self.ty, self.data(self.state())) // CKB,
                "active_shared_occupied": occupied(self.policy_lock, self.ty, shared) // CKB,
                "active_funded": self.active_capacity // CKB,
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ckb", required=True)
    parser.add_argument("--metatype", required=True, type=Path)
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
    c["metatype"] = (args.metatype / TARGET / "metatype").read_bytes()
    extra = (contracts / "payment-cap").read_bytes()
    with Devnet(args.ckb) as node:
        result = {
            "ckb_version": subprocess.check_output([args.ckb, "--version"], text=True).strip(),
            "ckb_sha256": hashlib.sha256(Path(args.ckb).read_bytes()).hexdigest(),
            "metatype_revision": revision(args.metatype),
            "quotacell_revision": "b3f488e47359361ea5d2549c152b8c0b418ebead",
            "baseline_sha256": BASELINE_SHA,
            "common_genesis_secp_cell_occupied_ckb": common_secp_capacity(node),
            "variants": {},
        }
        for key, contract_set in [("A", a), ("B", b), ("C", c), ("C+cap", {**c, "payment-cap": extra})]:
            print(f"Testing {key}", file=sys.stderr, flush=True)
            variant = "C" if key == "C+cap" else key
            result["variants"][key] = Pocket(node, variant, contract_set, 61 if key == "C+cap" else None).run()
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
