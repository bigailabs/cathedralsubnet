"""v3 wire types.

Every record that crosses a process boundary lives here. Anything written
to disk is canonicalized (sorted keys, ISO timestamps, no NaN) so
signatures replay-stable.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from enum import Enum
from typing import Any

import blake3
from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# enums
# ---------------------------------------------------------------------------


class TaskType(str, Enum):
    RESEARCH = "research"
    CODE_PATCH = "code_patch"
    TOOL_ROUTE = "tool_route"
    MULTI_STEP = "multi_step"
    CLASSIFY = "classify"
    BUG_REPRO = "bug_repro"


class FailureClass(str, Enum):
    NONE = "none"
    TOOL_MISUSE = "tool_misuse"
    HALLUCINATED_CITATION = "hallucinated_citation"
    WRONG_FORMAT = "wrong_format"
    TIMEOUT = "timeout"
    IRRELEVANT = "irrelevant"
    NO_OUTPUT = "no_output"
    AGENT_ERROR = "agent_error"


class CodingFailureClass(str, Enum):
    """Code-specific failure classes for bug_repro / test_gen / future
    coding task types. Used in addition to the generic FailureClass; the
    rubric chooses which enum to populate.
    """

    NONE = "none"
    COLLECTION_FAILED = "collection_failed"
    SANDBOX_VIOLATION = "sandbox_violation"
    FLAKE = "flake"
    NO_BUG_REPRO = "no_bug_repro"
    FIXED_COMMIT_FAILS = "fixed_commit_fails"
    MISSING_REPO_SYMBOL = "missing_repo_symbol"
    COVERAGE_GAMING = "coverage_gaming"
    MUTATION_THRESHOLD_MISS = "mutation_threshold_miss"


class TaskSplit(str, Enum):
    """Which downstream bucket a trajectory is allowed to flow into.

    Coding jobs default to OPERATOR_REVIEW until human-reviewed. Tasks
    used to score live miners must be OPERATOR_REVIEW or
    PUBLIC_LEADERBOARD; tasks reserved for held-out eval must NEVER be
    exported as training data.
    """

    TRAIN_EXPORTABLE = "train_exportable"
    PUBLIC_LEADERBOARD = "public_leaderboard"
    HELDOUT_EVAL = "heldout_eval"
    OPERATOR_REVIEW = "operator_review"


class DistillationReadiness(str, Enum):
    GOLD = "gold"
    PREFERENCE_WINNER = "preference_winner"
    PREFERENCE_LOSER = "preference_loser"
    NEGATIVE = "negative"
    DISCARD = "discard"


# ---------------------------------------------------------------------------
# job spec
# ---------------------------------------------------------------------------


class ToolDescriptor(BaseModel):
    """Catalog entry for a tool the miner may call."""

    model_config = ConfigDict(extra="forbid")

    name: str
    description: str
    args_schema: dict[str, Any] = Field(default_factory=dict)


class JobSpec(BaseModel):
    """What the validator asks for. Deterministic given (task_type, seed).

    For coding jobs (bug_repro, test_gen, ...), three load-bearing
    fields shape what flows into training data:

      - ``context`` is the **public** prompt context. The miner sees
        it; export pipelines may include it in training prompts.
      - ``hidden_context`` is validator-only. It carries oracles
        (fixed_commit_source, expected_symptom, mutation seed, ...).
        Export pipelines MUST refuse to emit it into training rows.
      - ``task_split`` decides which downstream bucket the trajectory
        may feed (train_exportable / public_leaderboard / heldout_eval
        / operator_review). Coding jobs default to operator_review.
    """

    model_config = ConfigDict(extra="forbid")

    job_id: str = Field(default_factory=lambda: f"job_{uuid.uuid4().hex[:12]}")
    task_type: TaskType
    prompt: str
    context: dict[str, Any] = Field(default_factory=dict)
    hidden_context: dict[str, Any] = Field(default_factory=dict)
    tools: list[ToolDescriptor] = Field(default_factory=list)
    expected_artifacts: list[str] = Field(default_factory=list)
    rubric_id: str
    seed: int = 0
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    deadline_seconds: float = 60.0
    task_split: TaskSplit = TaskSplit.TRAIN_EXPORTABLE

    def public_view(self) -> dict[str, Any]:
        """The miner-visible projection: everything except hidden_context."""
        return {
            "job_id": self.job_id,
            "task_type": self.task_type.value,
            "prompt": self.prompt,
            "context": dict(self.context),
            "tools": [t.model_dump(mode="json") for t in self.tools],
            "expected_artifacts": list(self.expected_artifacts),
            "rubric_id": self.rubric_id,
            "seed": self.seed,
            "deadline_seconds": self.deadline_seconds,
            "task_split": self.task_split.value,
        }


# ---------------------------------------------------------------------------
# tool call (one observation)
# ---------------------------------------------------------------------------


class ToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step: int
    tool_name: str
    args: dict[str, Any] = Field(default_factory=dict)
    result: Any = None
    ok: bool = True
    error: str | None = None
    started_at: datetime
    ended_at: datetime
    latency_ms: float

    @property
    def duration_ms(self) -> float:
        return self.latency_ms


# ---------------------------------------------------------------------------
# agent result (what the miner returns)
# ---------------------------------------------------------------------------


class AgentResult(BaseModel):
    """What the MinerAgent.run() returns."""

    model_config = ConfigDict(extra="forbid")

    final_output: str
    structured: dict[str, Any] = Field(default_factory=dict)
    artifacts: dict[str, str] = Field(default_factory=dict)  # name -> sha256
    model_id: str | None = None
    token_count: int | None = None
    wall_time_ms: float | None = None
    agent_error: str | None = None


# ---------------------------------------------------------------------------
# score
# ---------------------------------------------------------------------------


class ScoreParts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dimensions: dict[str, float] = Field(default_factory=dict)
    weighted: float = 0.0
    failure_class: FailureClass = FailureClass.NONE
    coding_failure: CodingFailureClass = CodingFailureClass.NONE
    verifier_metrics: dict[str, Any] = Field(default_factory=dict)
    readiness: DistillationReadiness = DistillationReadiness.DISCARD
    notes: str = ""

    @classmethod
    def empty(cls) -> ScoreParts:
        return cls()


# ---------------------------------------------------------------------------
# trajectory (the unit of data)
# ---------------------------------------------------------------------------


class Trajectory(BaseModel):
    """Job + tool trace + result + score, joined.

    This is the row of training data. Canonicalize via .canonical_bytes()
    before hashing or signing.
    """

    model_config = ConfigDict(extra="forbid")

    trajectory_id: str = Field(default_factory=lambda: f"traj_{uuid.uuid4().hex[:16]}")
    job: JobSpec
    miner_hotkey: str
    miner_kind: str  # "echo" | "heuristic" | "llm" | "polaris" | ...
    tool_calls: list[ToolCall] = Field(default_factory=list)
    result: AgentResult
    score: ScoreParts = Field(default_factory=ScoreParts.empty)
    started_at: datetime
    ended_at: datetime
    bundle_hash: str = ""  # filled by archive on persist

    def canonical_bytes(self) -> bytes:
        """Stable JSON byte representation for hashing/signing.

        Excludes bundle_hash itself (so the hash is a hash *of* the
        canonical body) and any signature fields (this is the input to
        the receipt signer, not the output).
        """
        d = self.model_dump(mode="json", exclude={"bundle_hash"})
        return json.dumps(d, sort_keys=True, separators=(",", ":")).encode("utf-8")

    def compute_bundle_hash(self) -> str:
        # BLAKE3 matches the v1 hashing convention used across publisher/evidence/eval.
        return blake3.blake3(self.canonical_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# receipt (the signed projection)
# ---------------------------------------------------------------------------


class Receipt(BaseModel):
    """Signed projection of a trajectory."""

    model_config = ConfigDict(extra="forbid")

    receipt_version: str = "v2"
    trajectory_id: str
    job_id: str
    task_type: TaskType
    miner_hotkey: str
    miner_kind: str
    score: float
    failure_class: FailureClass
    readiness: DistillationReadiness
    bundle_hash: str
    signed_at: datetime
    signature_scheme: str  # "ed25519" | "sr25519"
    signer_pubkey_hex: str
    signature_hex: str

    def signing_payload(self) -> bytes:
        """The bytes that go into sign()/verify(). Stable across implementations."""
        d = self.model_dump(mode="json", exclude={"signature_hex"})
        return json.dumps(d, sort_keys=True, separators=(",", ":")).encode("utf-8")


# ---------------------------------------------------------------------------
# weights
# ---------------------------------------------------------------------------


class Weights(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshot_id: str = Field(default_factory=lambda: f"w_{uuid.uuid4().hex[:12]}")
    computed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    per_miner: dict[str, float] = Field(default_factory=dict)  # hotkey -> weight
    trajectory_count: dict[str, int] = Field(default_factory=dict)
    half_life: int = 50
    on_chain: bool = False


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def canonical_json(obj: Any) -> bytes:
    """Deterministic JSON encoding used by hashing/signing across v3."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=_default).encode("utf-8")


def _default(o: Any) -> Any:
    if isinstance(o, datetime):
        return o.isoformat()
    if isinstance(o, Enum):
        return o.value
    if isinstance(o, BaseModel):
        return o.model_dump(mode="json")
    raise TypeError(f"unserializable: {type(o)}")
