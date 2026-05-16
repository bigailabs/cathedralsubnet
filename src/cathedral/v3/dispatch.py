"""bug_isolation_v1 dispatcher: parsed stdout -> scored result.

This is the publisher-side hook between the SSH Hermes runner (which
ferries stdout back from the miner box) and the static scorer. It
also owns the at-most-one-repair-prompt policy.

Wiring into the existing ``SshHermesRunner`` is left for the
follow-up PR (the runner today is hard-wired to the regulatory card
prompt; opening it to a capability switch is a non-trivial diff
that should land with the rest of the bug_isolation_v1 publisher
plumbing). For now, the dispatcher accepts stdout strings directly
so unit tests and the E2E test can exercise the path without an
SSH transport.

Public surface:
  - ``DispatchResult``: data class with either a parsed claim and
    a score, or a failure reason. Always returns a value; never
    raises into the orchestrator.
  - ``dispatch_bug_isolation_claim``: pure function over (stdout,
    oracle). Caller supplies the oracle (already loaded from the
    corpus); caller handles persistence and signing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from cathedral.v3.claim_extraction import (
    ClaimExtractionError,
    ExtractedClaim,
    extract_claim,
    is_repair_worthy,
)
from cathedral.v3.scoring.bug_isolation import (
    BugIsolationScoreParts,
    score_bug_isolation_claim,
)


@dataclass(frozen=True)
class DispatchResult:
    """Outcome of dispatching one miner stdout transcript.

    On success: ``claim`` and ``score`` are set; ``failure_reason``
    is ``None``.

    On any structured failure (parse error, schema mismatch,
    challenge-id mismatch): ``claim`` and ``score`` are ``None``;
    ``failure_reason`` carries a short slug suitable for logging
    and for the ``malformed_claim`` score path.

    The signed payload's ``weighted_score`` is ``0.0`` in the
    failure case; the publisher should still sign the row so the
    miner gets a verifiable failure record on the feed.
    """

    claim: ExtractedClaim | None
    score: BugIsolationScoreParts | None
    failure_reason: str | None
    repair_was_attempted: bool

    @property
    def ok(self) -> bool:
        return self.claim is not None and self.score is not None


def dispatch_bug_isolation_claim(
    *,
    expected_challenge_id: str,
    oracle_culprit_file: str,
    oracle_culprit_symbol: str | None,
    oracle_line_range: tuple[int, int],
    oracle_required_keywords: tuple[str, ...],
    stdout: str,
    repair_stdout: str | None = None,
) -> DispatchResult:
    """Parse stdout, validate, score against the oracle.

    Args:
      expected_challenge_id: The challenge_id Cathedral issued.
        If the parsed claim's challenge_id doesn't match, the
        result is a failure (``challenge_id_mismatch``) regardless
        of how good the rest of the claim looks. Protects against
        miners caching answers from prior epochs.
      oracle_*: hidden ground truth for this challenge. Caller
        loads it from the corpus; this function never touches the
        corpus directly.
      stdout: raw transcript from the first Hermes invocation.
      repair_stdout: optional second transcript from the single
        repair attempt the publisher made. If the first stdout
        parses cleanly, ``repair_stdout`` is ignored. If the
        first one fails AND was repair-worthy, the caller fired
        the repair prompt and passes the second transcript here.
    """
    claim, parse_failure = _try_parse(stdout)
    repair_was_attempted = False
    if claim is None:
        assert parse_failure is not None
        if repair_stdout is not None and is_repair_worthy(parse_failure):
            repair_was_attempted = True
            claim, parse_failure = _try_parse(repair_stdout)
        if claim is None:
            assert parse_failure is not None
            return DispatchResult(
                claim=None,
                score=None,
                failure_reason=parse_failure.reason,
                repair_was_attempted=repair_was_attempted,
            )

    if claim.challenge_id != expected_challenge_id:
        return DispatchResult(
            claim=claim,
            score=None,
            failure_reason="challenge_id_mismatch",
            repair_was_attempted=repair_was_attempted,
        )

    score = score_bug_isolation_claim(
        claim=claim.to_dict(),
        oracle_culprit_file=oracle_culprit_file,
        oracle_culprit_symbol=oracle_culprit_symbol,
        oracle_line_range=oracle_line_range,
        oracle_required_keywords=oracle_required_keywords,
    )
    return DispatchResult(
        claim=claim,
        score=score,
        failure_reason=None,
        repair_was_attempted=repair_was_attempted,
    )


def _try_parse(
    stdout: str,
) -> tuple[ExtractedClaim | None, ClaimExtractionError | None]:
    try:
        return extract_claim(stdout), None
    except ClaimExtractionError as e:
        return None, e


__all__ = ["DispatchResult", "dispatch_bug_isolation_claim"]
