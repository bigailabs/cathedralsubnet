"""Cathedral v4 — publisher-side private challenge runtime.

**Architectural position (REVISED 2026-05-17):**

v4 is a publisher-side runtime, NOT an inline validator engine.

  * The publisher's worker host loads private synthetic rows,
    scrambles upstream vault repos, applies the synthetic bug
    server-side, builds the broken-state bundle the miner sees,
    runs the miner's fix patch under a bounded hermetic oracle,
    packages the signed envelope, and persists it.
  * The validator NEVER executes any of this. Validators only pull
    signed v4 rows from the publisher feed and verify the
    Ed25519 signature via ``cathedral.v4.verify.verify_v4_row``.
    No subprocess, no patch-runner, no file I/O on the validator
    side.

Public surface — importers should pin to ``cathedral.v4.<name>``:

  * ``CathedralEngine`` (publisher)
  * ``build_signed_v4_row`` (publisher signer wiring)
  * ``verify_v4_row`` (validator-only)
  * ``AgentTurn`` / ``MinerTrajectory`` / ``ValidationPayload`` (wire)
  * ``MinerArena`` / ``IsomorphicScrambler`` / ``ScrambledRepo``
    (publisher-side workspace synthesis)
  * ``run_patch_against_hidden_test`` / ``PatchRunResult`` (publisher
    oracle entry point; never call from a validator)

Coexistence: v4 lives alongside v3 (no v3 files modified). The v3
publisher continues to serve bug_isolation rows; v4 adds the
patch-validator capability behind a separate task_type once the feed
flip lands in a follow-up PR.
"""

from cathedral.v4.arena import (
    ArenaError,
    IsomorphicScrambler,
    MinerArena,
    ScrambledRepo,
)
from cathedral.v4.cathedral_engine import (
    ONE_SHOT_TURN_THRESHOLD,
    SIPHON_FLAG_ONE_SHOT,
    CathedralEngine,
    EngineError,
)
from cathedral.v4.oracle import (
    OracleError,
    PatchRunResult,
    run_patch_against_hidden_test,
)
from cathedral.v4.schemas import (
    AgentTurn,
    MinerTrajectory,
    ValidationPayload,
)
from cathedral.v4.sign import build_signed_v4_row
from cathedral.v4.verify import VerifyError, verify_v4_row

__all__ = [
    "ONE_SHOT_TURN_THRESHOLD",
    "SIPHON_FLAG_ONE_SHOT",
    "AgentTurn",
    "ArenaError",
    "CathedralEngine",
    "EngineError",
    "IsomorphicScrambler",
    "MinerArena",
    "MinerTrajectory",
    "OracleError",
    "PatchRunResult",
    "ScrambledRepo",
    "ValidationPayload",
    "VerifyError",
    "build_signed_v4_row",
    "run_patch_against_hidden_test",
    "verify_v4_row",
]
