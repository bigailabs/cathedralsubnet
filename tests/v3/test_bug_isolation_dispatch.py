"""Dispatcher tests: stdout -> parsed claim -> score (or failure).

Covers the at-most-one-repair-prompt policy, challenge_id mismatch
rejection, and the malformed-claim score path.
"""

from __future__ import annotations

from cathedral.v3.dispatch import dispatch_bug_isolation_claim


_ORACLE = {
    "oracle_culprit_file": "src/foo/bar.py",
    "oracle_culprit_symbol": "Bar.method",
    "oracle_line_range": (100, 120),
    "oracle_required_keywords": ("off-by-one", "loop", "boundary"),
}


def _wrap_final(blob: str) -> str:
    return f"reasoning\n```FINAL_ANSWER\n{blob}\n```\n"


def _good_blob(challenge_id: str = "ch_x") -> str:
    return (
        '{"challenge_id": "' + challenge_id + '",'
        ' "culprit_file": "src/foo/bar.py",'
        ' "culprit_symbol": "Bar.method",'
        ' "line_range": [100, 120],'
        ' "failure_mode": "off-by-one loop boundary"}'
    )


def test_happy_path_scores_full_marks() -> None:
    r = dispatch_bug_isolation_claim(
        expected_challenge_id="ch_x",
        stdout=_wrap_final(_good_blob()),
        **_ORACLE,
    )
    assert r.ok
    assert r.failure_reason is None
    assert r.score is not None
    assert r.score.weighted_score == 1.0
    assert r.repair_was_attempted is False


def test_challenge_id_mismatch_rejects_even_perfect_claim() -> None:
    """Miner attempts to cache an old epoch's answer."""
    r = dispatch_bug_isolation_claim(
        expected_challenge_id="ch_x",
        stdout=_wrap_final(_good_blob("ch_stale")),
        **_ORACLE,
    )
    assert not r.ok
    assert r.failure_reason == "challenge_id_mismatch"
    # Claim is preserved so the publisher can sign a failure row
    # that names what the miner sent.
    assert r.claim is not None
    assert r.claim.challenge_id == "ch_stale"


def test_malformed_stdout_returns_failure_reason() -> None:
    r = dispatch_bug_isolation_claim(
        expected_challenge_id="ch_x",
        stdout="just prose, no JSON",
        **_ORACLE,
    )
    assert not r.ok
    assert r.failure_reason == "no_json_block_found"


def test_repair_attempt_used_for_repair_worthy_first_failure() -> None:
    """First stdout is unparseable; repair stdout is well-formed.
    Dispatcher should consume the repair attempt and succeed."""
    r = dispatch_bug_isolation_claim(
        expected_challenge_id="ch_x",
        stdout="no json here",
        repair_stdout=_wrap_final(_good_blob()),
        **_ORACLE,
    )
    assert r.ok
    assert r.score is not None
    assert r.score.weighted_score == 1.0
    assert r.repair_was_attempted is True


def test_repair_not_attempted_when_first_was_wrong_shape() -> None:
    """If the first stdout parsed as valid JSON but missed required
    fields, the agent understands JSON, just not the schema. Repair
    prompts rarely help in that case; the policy is to skip them.
    """
    bad = '{"challenge_id": "ch_x"}'  # missing required fields
    r = dispatch_bug_isolation_claim(
        expected_challenge_id="ch_x",
        stdout=_wrap_final(bad),
        repair_stdout=_wrap_final(_good_blob()),  # would succeed if tried
        **_ORACLE,
    )
    assert not r.ok
    assert r.failure_reason == "missing_required_fields"
    assert r.repair_was_attempted is False


def test_partial_match_still_signs_partial_score() -> None:
    """Only file matches; dispatcher returns ok with partial score
    so the publisher signs a real row, not a malformed_claim row."""
    partial = (
        '{"challenge_id": "ch_x",'
        ' "culprit_file": "src/foo/bar.py",'  # matches
        ' "culprit_symbol": "totally.wrong",'
        ' "line_range": [1, 2],'
        ' "failure_mode": "unrelated guess"}'
    )
    r = dispatch_bug_isolation_claim(
        expected_challenge_id="ch_x",
        stdout=_wrap_final(partial),
        **_ORACLE,
    )
    assert r.ok
    assert r.score is not None
    assert r.score.weighted_score == 0.35  # only file slice
