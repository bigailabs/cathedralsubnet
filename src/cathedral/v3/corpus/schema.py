"""bug_isolation_v1 challenge row schema.

Public-facing fields are sent to the miner inside the Hermes prompt.
Hidden fields stay on the publisher and are used only for scoring.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ChallengeRow(BaseModel):
    """One curated bug_isolation_v1 challenge.

    The shape is locked so corpus seed JSON, the sampler, and the
    scorer all read the same fields. New optional fields are fine;
    renames or required-field additions are breaking.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    # --- public (sent to miner) ------------------------------------
    id: str = Field(..., description="Stable seed for challenge_id derivation.")
    repo: str = Field(..., description="Public git URL (https).")
    commit: str = Field(
        ...,
        pattern=r"^[0-9a-f]{40}$",
        description=(
            "40-char lowercase hex SHA of the parent-of-fix commit "
            "(the broken tree the miner inspects). Validated by regex "
            "so typos and accidental uppercase fail at row construction, "
            "not at runtime."
        ),
    )
    issue_text: str = Field(
        ...,
        description=(
            "Paraphrased symptom. Must not contain CVE IDs or verbatim "
            "advisory text. This is what the miner sees."
        ),
    )

    # --- hidden (publisher-only, never sent to miner) --------------
    culprit_file: str = Field(..., description="Hidden oracle: file the bug lives in.")
    culprit_symbol: str | None = Field(
        default=None,
        description=(
            "Hidden oracle: function/method/class the bug lives in. "
            "Optional: some bugs are file-level (module-level mutable "
            "default, import-time crash, config error) and have no "
            "meaningful symbol. When unset, the symbol slice of the "
            "score is zero and total score is capped at 0.80."
        ),
    )
    line_range: tuple[int, int] = Field(
        ..., description="Hidden oracle: (start, end) inclusive lines."
    )
    required_failure_keywords: tuple[str, ...] = Field(
        ...,
        min_length=1,
        description=(
            "Hidden oracle: substrings the miner's failure_mode must "
            "match (case-insensitive). Threshold is ceil(n/2) with a "
            "floor of 1."
        ),
    )

    # --- metadata --------------------------------------------------
    difficulty: str = Field(
        default="medium",
        description="Hint for sampling/UI; one of easy|medium|hard.",
    )
    bucket: str = Field(
        default="general",
        description=(
            "Topical bucket for sampling diversity later (e.g. "
            "off_by_one, mutable_default, type_confusion). Free-form."
        ),
    )

    # --- provenance (required) -------------------------------------
    source_url: str = Field(
        ...,
        description=(
            "Real upstream fix commit, PR, or GHSA URL that proves the "
            "oracle. A reviewer must be able to follow this link and "
            "confirm culprit_file, line_range, and failure_mode are "
            "not invented. No row without a real source is allowed in "
            "the production corpus."
        ),
    )

    def public_view(self) -> dict[str, str]:
        """The subset the miner sees in the Hermes prompt."""
        return {
            "challenge_id": f"ch_{self.id}",
            "repo": self.repo,
            "commit": self.commit,
            "issue_text": self.issue_text,
        }
