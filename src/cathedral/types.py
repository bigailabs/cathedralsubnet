"""Wire types shared by validator, miner, and tests.

Every record that crosses a process boundary is defined here. Downstream
modules import these and never invent parallel definitions.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

# --------------------------------------------------------------------------
# Identifiers
# --------------------------------------------------------------------------


class Hotkey(str):
    """Bittensor hotkey ss58. Wrapped str for type clarity."""

    __slots__ = ()


# --------------------------------------------------------------------------
# Claim — issue #2 wire format
# --------------------------------------------------------------------------


class ClaimVersion(str, Enum):
    V1 = "v1"


class PolarisAgentClaim(BaseModel):
    """`cathedral.polaris_agent_claim.v1`.

    Submitted by miners to `POST /v1/claim`. The validator queues the claim,
    fetches Polaris evidence by identifier, verifies signatures, and scores
    the resulting card.
    """

    model_config = ConfigDict(extra="forbid")

    type: str = Field(default="cathedral.polaris_agent_claim.v1")
    version: ClaimVersion = ClaimVersion.V1
    miner_hotkey: str
    owner_wallet: str
    work_unit: str
    polaris_agent_id: str
    polaris_deployment_id: str | None = None
    polaris_run_ids: list[str] = Field(default_factory=list)
    polaris_artifact_ids: list[str] = Field(default_factory=list)
    submitted_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("miner_hotkey", "owner_wallet", "work_unit", "polaris_agent_id")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v:
            raise ValueError("must be non-empty")
        return v


# --------------------------------------------------------------------------
# Polaris records — what the validator pulls and verifies
# --------------------------------------------------------------------------


class PolarisManifest(BaseModel):
    model_config = ConfigDict(extra="allow")

    polaris_agent_id: str
    owner_wallet: str
    created_at: datetime
    schema_: str = Field(alias="schema")
    signature: str


class PolarisRunRecord(BaseModel):
    model_config = ConfigDict(extra="allow")

    run_id: str
    polaris_agent_id: str
    started_at: datetime
    ended_at: datetime | None = None
    outcome: str
    signature: str


class PolarisArtifactRecord(BaseModel):
    model_config = ConfigDict(extra="allow")

    artifact_id: str
    polaris_agent_id: str
    run_id: str | None = None
    content_url: str
    content_hash: str
    report_hash: str | None = None
    signature: str


class ConsumerClass(str, Enum):
    EXTERNAL = "external"
    CREATOR = "creator"
    PLATFORM = "platform"
    TEST = "test"
    SELF_LOOP = "self_loop"

    def counts_for_rewards(self) -> bool:
        return self is ConsumerClass.EXTERNAL


class PolarisUsageRecord(BaseModel):
    model_config = ConfigDict(extra="allow")

    usage_id: str
    polaris_agent_id: str
    consumer: ConsumerClass
    consumer_wallet: str | None = None
    used_at: datetime
    flagged: bool = False
    refunded: bool = False
    signature: str


class EvidenceBundle(BaseModel):
    """The verified payload Cathedral hands to the card scorer."""

    manifest: PolarisManifest
    runs: list[PolarisRunRecord] = Field(default_factory=list)
    artifacts: list[PolarisArtifactRecord] = Field(default_factory=list)
    usage: list[PolarisUsageRecord] = Field(default_factory=list)
    verified_at: datetime
    filtered_usage_count: int = 0


# --------------------------------------------------------------------------
# Cards — issue #3
# --------------------------------------------------------------------------


class Jurisdiction(str, Enum):
    EU = "eu"
    US = "us"
    UK = "uk"
    CA = "ca"
    AU = "au"
    IN = "in"
    BR = "br"
    OTHER = "other"


class SourceClass(str, Enum):
    GOVERNMENT = "government"
    REGULATOR = "regulator"
    COURT = "court"
    PARLIAMENT = "parliament"
    LAW_TEXT = "law_text"
    OFFICIAL_JOURNAL = "official_journal"
    SECONDARY_ANALYSIS = "secondary_analysis"
    OTHER = "other"


OFFICIAL_SOURCE_CLASSES: frozenset[SourceClass] = frozenset(
    {
        SourceClass.GOVERNMENT,
        SourceClass.REGULATOR,
        SourceClass.COURT,
        SourceClass.PARLIAMENT,
        SourceClass.LAW_TEXT,
        SourceClass.OFFICIAL_JOURNAL,
    }
)


class Source(BaseModel):
    url: str
    class_: SourceClass = Field(alias="class")
    fetched_at: datetime
    status: int
    content_hash: str

    model_config = ConfigDict(populate_by_name=True)


class Card(BaseModel):
    id: str
    jurisdiction: Jurisdiction
    topic: str
    worker_owner_hotkey: str
    polaris_agent_id: str
    title: str
    summary: str
    what_changed: str
    why_it_matters: str
    action_notes: str
    risks: str
    citations: list[Source]
    confidence: float = Field(ge=0.0, le=1.0)
    no_legal_advice: bool
    last_refreshed_at: datetime
    refresh_cadence_hours: int = Field(gt=0)


class ScoreParts(BaseModel):
    """Six dimensions per issue #3. Each in [0, 1]."""

    source_quality: float = 0.0
    freshness: float = 0.0
    specificity: float = 0.0
    usefulness: float = 0.0
    clarity: float = 0.0
    maintenance: float = 0.0

    def weighted(self) -> float:
        """Default weighting: source quality and maintenance lead."""
        return (
            0.30 * self.source_quality
            + 0.20 * self.maintenance
            + 0.15 * self.freshness
            + 0.15 * self.specificity
            + 0.10 * self.usefulness
            + 0.10 * self.clarity
        )


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def canonical_json_for_signing(record: dict[str, Any]) -> bytes:
    """Stable bytes used as the signature payload.

    Drops the `signature` key and serializes with sorted keys + no whitespace.
    Both Polaris (signer) and Cathedral (verifier) must agree on this exact
    canonicalization or all verification fails.
    """
    import json

    payload = {k: v for k, v in record.items() if k != "signature"}
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
