# Pocket security model

This experiment covers native CKB capacity and is unaudited.

## Authority and conservation

Fund Locks in B and C commit to the complete policy Type Script hash and the owner's authorising Lock hash. An empty, untyped input with that owner Lock grants independent recovery once CKB validates its Lock. The owner can therefore recover before or after retirement. An attached Type still has to pass its own validation. Empty untyped native deposits are the supported delegated asset. A native deposit with unexpected data can be recovered by the owner in B and C, and any extra Type may narrow recovery under its own rules.

A delegate must use the genuine live policy input at index zero and an empty, untyped authorising input for one of the two registered delegates. The fund group checks that the same fund Lock holds the policy. In C, the policy input must also include the quota rule in its resolved manifest, and the chosen physical configuration pins the quota adapter. The quota checker requires that the actor is enabled, the payout goes to that delegate's approved Lock, and the balance is conserved between the protected inputs, protected change and the recipient. Fees come from the actor input. Shared and per-delegate counters advance by the payout, with checked integer arithmetic. The owner can change limits and revoke or re-enable a grant without clearing spent authority.

The contract recognises security by exact Lock hash. A builder must resolve that hash to the intended authorising script and verify its behaviour. The experiment uses CKB's genesis secp256k1 Sighash Lock and five distinct keys. A delegate cannot obtain owner authorisation with their own key. Keys in this experiment are regenerated each run.

## Policy identity and retirement

There is exactly one active policy output for a Pocket. Its namespace is calculated from the actual first consumed CKB input and the zero output index. The owner must authenticate that creation. A second output of the same policy group is rejected. Every active transition checks the complete physical group, preserves the relevant Lock and validates the next quota state. In C, the adapter also requires that its virtual quota group covers the complete physical policy group.

Closure consumes the active policy under owner authority and sends its capacity and any included native funds to the owner. The original creation input has already been spent, so a new transaction cannot recreate the same namespace. A different Pocket produces a different physical Type hash. No conclusion about retirement is taken from an absent policy input. The independent recovery branch relies on owner authorisation, while delegates require the actual live policy input.

B and C implement independent recovery. Preserving A's strict terminal-state requirement would need authenticated retirement evidence in the recovery path. This experiment does not implement such a proof.

## Composition and limits

C+cap adds an independently pinned second rule. It reads the actual payout from the quota transaction interface and sees the same physical group through the dispatcher. An input-side cap cannot be omitted from the successor while a policy remains. Its adapter can retire when the policy is consumed. The quota adapter binary is identical in C and C+cap. The cap rule relies on the common convention that an active delegated payment places its payout at output two.

The single active policy Cell is a contention point in A, B and C. Competing delegated spends need to use successive policy outpoints and sign against the current state. Independent owner recovery in B and C can proceed without that state, subject to contention on the selected fund and authorising inputs. C also needs its committed adapter code, table, genesis header, and any used shared definition while the policy is active. Owner recovery uses only the immutable fund Lock and the owner authorising Lock. Deployments must preserve access to the code Cells.

The replay uses standard fee and change shapes and bounds delegates to 64 fund inputs. It covers two-fund aggregation. It does not exercise maximum-size groups, reorganisation recovery, production wallets, UDT deposits or strict post-closure recovery with zero retained policy capacity.
