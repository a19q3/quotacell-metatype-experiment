# Run notes

- The first RISC-V build emitted atomic instructions. CKB rejected the dispatcher with an invalid instruction. Both build scripts now lower atomic operations and disable compressed instruction generation as compiler flags, then record compiled data hashes.
- Rebuilding upstream QCN4 with the available Linux Clang produced a different ELF digest. The comparison uses the byte-identical public A, F and Q ELFs from the pinned deployment transactions. The acquisition command verifies their identities.
- The initial baseline transaction builder used Data2 for the A and F provider Scripts. The verifier rejected the shape. The published formats use Data1 for those two providers and Data2 for Q.
- A first common replenishment of 100 CKB was below the 105 CKB occupied minimum for the owner-bound fund Lock. The matched lifecycle now uses 110 CKB for replenishment and 105 CKB for the late deposit.
- Configured public RPC proxy routes rejected the initial binary retrieval. Direct reads of the committed deployment transactions succeeded and the SHA-256 checks passed. No public transactions were submitted.
