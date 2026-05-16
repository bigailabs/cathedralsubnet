"""Tests for the bug_isolation_v1 claim extractor.

Locks the FINAL_ANSWER block preference, the fenced-json fallback,
the brace-balanced scan-from-end fallback, and the schema
validation surface that the malformed_claim score path depends on.
"""

from __future__ import annotations

import pytest

from cathedral.v3.claim_extraction import (
    ClaimExtractionError,
    extract_claim,
    is_repair_worthy,
)


def _good_claim_json() -> str:
    return (
        '{"challenge_id": "ch_x",'
        ' "culprit_file": "src/foo/bar.py",'
        ' "culprit_symbol": "Bar.method",'
        ' "line_range": [100, 120],'
        ' "failure_mode": "off-by-one in loop",'
        ' "repro_input": "Bar().method([])",'
        ' "explanation": "first reasoning"}'
    )


# --------------------------------------------------------------------------
# FINAL_ANSWER path
# --------------------------------------------------------------------------


def test_final_answer_block_wins_over_other_json() -> None:
    """The FINAL_ANSWER block is the canonical contract path and must
    win even if other JSON shows up earlier in the transcript."""
    stdout = (
        "Some reasoning here.\n"
        "```json\n"
        '{"challenge_id": "ch_decoy", "culprit_file": "decoy.py",'
        ' "line_range": [1, 2], "failure_mode": "decoy"}'
        "\n```\n"
        "Final thoughts.\n"
        "```FINAL_ANSWER\n"
        f"{_good_claim_json()}\n"
        "```\n"
    )
    c = extract_claim(stdout)
    assert c.challenge_id == "ch_x"
    assert c.culprit_file == "src/foo/bar.py"


def test_final_answer_block_is_case_insensitive() -> None:
    stdout = (
        "```final_answer\n"
        f"{_good_claim_json()}\n"
        "```\n"
    )
    c = extract_claim(stdout)
    assert c.challenge_id == "ch_x"


# --------------------------------------------------------------------------
# Fenced json fallback
# --------------------------------------------------------------------------


def test_json_fence_fallback_when_no_final_answer() -> None:
    stdout = (
        "Here is my answer.\n"
        "```json\n"
        f"{_good_claim_json()}\n"
        "```\n"
    )
    c = extract_claim(stdout)
    assert c.challenge_id == "ch_x"


def test_last_json_fence_wins_when_multiple() -> None:
    """Last fenced json block is treated as the model's final word."""
    early = (
        '{"challenge_id": "ch_early", "culprit_file": "a.py",'
        ' "line_range": [1, 2], "failure_mode": "early"}'
    )
    stdout = (
        "```json\n" + early + "\n```\n"
        "I changed my mind.\n"
        "```json\n" + _good_claim_json() + "\n```\n"
    )
    c = extract_claim(stdout)
    assert c.challenge_id == "ch_x"


# --------------------------------------------------------------------------
# Brace-balanced scan fallback
# --------------------------------------------------------------------------


def test_brace_balanced_scan_picks_last_object() -> None:
    """No fences anywhere; scanner should still recover the last
    well-formed JSON object."""
    stdout = (
        "Reasoning trace: I think the bug is in bar.py.\n"
        "Earlier I considered " + _good_claim_json() + "\n"
        "but my final answer is " + _good_claim_json().replace("ch_x", "ch_final") + "\n"
    )
    c = extract_claim(stdout)
    assert c.challenge_id == "ch_final"


def test_brace_scan_skips_unbalanced_garbage() -> None:
    stdout = (
        "Open brace { without close should not fool us.\n"
        "Real answer: " + _good_claim_json() + "\n"
    )
    c = extract_claim(stdout)
    assert c.challenge_id == "ch_x"


# --------------------------------------------------------------------------
# Failure modes
# --------------------------------------------------------------------------


def test_empty_stdout_raises() -> None:
    with pytest.raises(ClaimExtractionError) as ei:
        extract_claim("")
    assert ei.value.reason == "no_json_block_found"
    assert is_repair_worthy(ei.value) is True


def test_no_json_anywhere_raises() -> None:
    with pytest.raises(ClaimExtractionError) as ei:
        extract_claim("just prose, no braces here at all")
    assert ei.value.reason == "no_json_block_found"
    assert is_repair_worthy(ei.value) is True


def test_invalid_json_in_final_answer_block_raises() -> None:
    stdout = "```FINAL_ANSWER\n{this is not valid json}\n```\n"
    with pytest.raises(ClaimExtractionError) as ei:
        extract_claim(stdout)
    assert ei.value.reason == "json_decode_failed"
    assert is_repair_worthy(ei.value) is True


def test_valid_json_wrong_shape_is_not_repair_worthy() -> None:
    """If the agent returned valid JSON but missed required fields,
    a repair prompt is unlikely to help; they understand JSON, they
    just don't follow the schema."""
    stdout = (
        "```FINAL_ANSWER\n"
        '{"challenge_id": "ch_x"}'
        "\n```\n"
    )
    with pytest.raises(ClaimExtractionError) as ei:
        extract_claim(stdout)
    assert ei.value.reason == "missing_required_fields"
    assert is_repair_worthy(ei.value) is False


def test_bad_line_range_raises() -> None:
    bad = _good_claim_json().replace("[100, 120]", "[200, 100]")
    stdout = f"```FINAL_ANSWER\n{bad}\n```\n"
    with pytest.raises(ClaimExtractionError) as ei:
        extract_claim(stdout)
    assert ei.value.reason == "bad_line_range"


def test_line_range_wrong_length_raises() -> None:
    bad = _good_claim_json().replace("[100, 120]", "[100, 110, 120]")
    stdout = f"```FINAL_ANSWER\n{bad}\n```\n"
    with pytest.raises(ClaimExtractionError) as ei:
        extract_claim(stdout)
    assert ei.value.reason == "bad_line_range"


def test_line_range_non_int_raises() -> None:
    bad = _good_claim_json().replace("[100, 120]", '["a", "b"]')
    stdout = f"```FINAL_ANSWER\n{bad}\n```\n"
    with pytest.raises(ClaimExtractionError) as ei:
        extract_claim(stdout)
    assert ei.value.reason == "bad_line_range"


def test_wrong_type_for_optional_field_raises() -> None:
    bad = _good_claim_json().replace('"Bar.method"', "12345")
    stdout = f"```FINAL_ANSWER\n{bad}\n```\n"
    with pytest.raises(ClaimExtractionError) as ei:
        extract_claim(stdout)
    assert ei.value.reason == "wrong_field_type"


# --------------------------------------------------------------------------
# Optional fields handled gracefully
# --------------------------------------------------------------------------


def test_culprit_symbol_optional_null_accepted() -> None:
    """File-level bugs have no symbol; null must parse."""
    blob = (
        '{"challenge_id": "ch_x",'
        ' "culprit_file": "src/foo/bar.py",'
        ' "culprit_symbol": null,'
        ' "line_range": [100, 120],'
        ' "failure_mode": "module-level config bug"}'
    )
    stdout = f"```FINAL_ANSWER\n{blob}\n```\n"
    c = extract_claim(stdout)
    assert c.culprit_symbol is None


def test_culprit_symbol_absent_accepted() -> None:
    """Same as null but with the field omitted entirely."""
    blob = (
        '{"challenge_id": "ch_x",'
        ' "culprit_file": "src/foo/bar.py",'
        ' "line_range": [100, 120],'
        ' "failure_mode": "module-level config bug"}'
    )
    stdout = f"```FINAL_ANSWER\n{blob}\n```\n"
    c = extract_claim(stdout)
    assert c.culprit_symbol is None
    assert c.repro_input is None
    assert c.explanation is None


def test_to_dict_round_trips_wire_shape() -> None:
    stdout = f"```FINAL_ANSWER\n{_good_claim_json()}\n```\n"
    c = extract_claim(stdout)
    d = c.to_dict()
    assert d["challenge_id"] == "ch_x"
    assert d["line_range"] == [100, 120]  # list, not tuple, for JSON
    assert d["culprit_symbol"] == "Bar.method"
    assert d["repro_input"] == "Bar().method([])"
