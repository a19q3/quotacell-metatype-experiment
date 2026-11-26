# Payment positions and references

The independent cap adapter reads a small transaction interface. The interface defines the old policy Cell, its successor, protected change, and the recipient payment. It applies to the experiment's native CKB transaction shape.

| Approach | Carrier | What the adapter checks |
| --- | --- | --- |
| Fixed positions | Agreed absolute indices | Roles at input 0 and outputs 0, 1 and 2 |
| Explicit semantic references | Cap state in the actual successor Cell Data | The four declared roles against the actual transaction |
| Group Message references | The group-scoped Metatype witness Message | The same roles, from the exact deployment and logical Script's witness |

In the explicit variants the reference bytes are ASCII `P`, version byte `1`, then four little-endian u16 absolute indices in the order above. Group Message mode uses action 1 and empty Proof and Seal. The regular authorising Locks continue to authenticate the CKB transaction. The Group Message is application context and confers no spending authority.

The cap takes the physical policy identity from the dispatcher and checks that the old and successor references point to those actual policy Cells. It identifies the protected Lock from the old policy Cell. Protected change must be empty untyped CKB under that Lock. The payment must be a distinct empty untyped output, distinct from the optional actor change. The sum of change and payment must equal the actual protected inputs. Quota independently enforces the authorised recipient and both counters. The cap's continuing-payment branch uses the four roles and capacities without decoding Quota's counters, grants or state layout.

The successor-state variant reads references only from the current transaction's output. Its previous state can contain the previous payment's references and has no authority over new positions. An administrative successor clears those fields. The witness variant keeps cap state empty and carries the references as transaction context. Incorrect role references are rejected. A test with reordered outputs and corresponding valid-looking references is still rejected by the combined policy because the original Quota adapter retains its fixed layout.

For output-only attachment to an existing policy, the cap independently checks the unchanged policy Lock, the owner-authorising input, absence of fund inputs and an exact Manifest diff adding only itself. The existing quota bytes are compared opaquely. No payment references are used for issuance, attachment, administration or retirement.

The three modes share one cap ELF. An eight-byte little-endian limit selects fixed positions. Prefix the same limit with byte 1 for successor-state references or byte 2 for Group Message references. Different args identify different logical groups. Results compare these modes in fresh Pockets under identical quota and cap terms. [Measured costs](RESULTS-v0.1.1.md)
