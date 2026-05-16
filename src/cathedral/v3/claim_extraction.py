"""Parse a bug_isolation_v1 claim out of a Hermes stdout transcript.

The agent contract (spec §5.2) tells the miner to reply with **only**
a fenced JSON block tagged ``FINAL_ANSWER``. Reality is messier:
agents prepend reasoning, append commentary, or wrap the JSON in
multiple code fences. This module handles those variants without
forking a second JSON parser.

Extraction order:
  1. Fenced ``FINAL_ANSWER`` JSON block, anywhere in stdout.
  2. Fenced ``json`` block, last one wins (closest to the model's
     terminal answer).
  3. Brace-balanced scan from the end of stdout for the last
     well-formed JSON object.
  4. Give up: caller gets a structured ``ClaimExtractionError``
     and can decide whether to fire the one allowed repair prompt.

Claim shape validation (spec §5.2):

  required: challenge_id, culprit_file, line_range (len 2, start<=end),
            failure_mode
  optional: culprit_symbol, repro_input, explanation

The caller is responsible for matching ``challenge_id`` to the
challenge it issued. This module just parses what came back; it
does not own the challenge map.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any


# ``FINAL_ANSWER`` block: ```FINAL_ANSWER\n{...}\n```
# Tolerate optional language hint after the tag and trailing whitespace.
_FINAL_ANSWER_RE = re.compile(
    r"```\s*FINAL_ANSWER\b[^\n]*\n(.*?)\n```",
    re.DOTALL | re.IGNORECASE,
)

# Any fenced ```json ... ``` block. Used as fallback.
_JSON_BLOCK_RE = re.compile(
    r"```\s*json\b[^\n]*\n(.*?)\n```",
    re.DOTALL | re.IGNORECASE,
)


class ClaimExtractionError(Exception):
    """Caller-visible reason a claim could not be parsed.

    The ``reason`` slug is suitable for logging and for the
    ``malformed_claim`` score path.
    """

    def __init__(self, reason: str, detail: str | None = None) -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True)
class ExtractedClaim:
    """Validated claim ready for scoring.

    Field shapes match the wire schema. ``line_range`` is a 2-tuple
    of ``int``; ``culprit_symbol``, ``repro_input``, ``explanation``
    may be ``None``.
    """

    challenge_id: str
    culprit_file: str
    culprit_symbol: str | None
    line_range: tuple[int, int]
    failure_mode: str
    repro_input: str | None
    explanation: str | None

    def to_dict(self) -> dict[str, Any]:
        """Wire-shape dict for embedding in the signed payload."""
        return {
            "challenge_id": self.challenge_id,
            "culprit_file": self.culprit_file,
            "culprit_symbol": self.culprit_symbol,
            "line_range": list(self.line_range),
            "failure_mode": self.failure_mode,
            "repro_input": self.repro_input,
            "explanation": self.explanation,
        }


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------


def extract_claim(stdout: str) -> ExtractedClaim:
    """Parse ``stdout`` and return a validated claim, or raise.

    Caller is expected to wrap this in try/except and surface
    ``ClaimExtractionError.reason`` into the malformed-claim score
    path. Caller is also responsible for the at-most-one-repair-
    prompt retry policy (this module does not loop or SSH).
    """
    raw_obj = _find_json_object(stdout)
    return _validate_claim(raw_obj)


def is_repair_worthy(error: ClaimExtractionError) -> bool:
    """Cheap heuristic for whether to spend the single repair attempt.

    Repair if the parser found nothing or found something that
    wasn't valid JSON; do NOT repair if a valid JSON object came
    back with the wrong shape (the agent is misbehaving and a
    repair prompt is unlikely to help).
    """
    return error.reason in {"no_json_block_found", "json_decode_failed"}


# --------------------------------------------------------------------------
# Stdout scanning
# --------------------------------------------------------------------------


def _find_json_object(stdout: str) -> dict[str, Any]:
    if not isinstance(stdout, str) or not stdout.strip():
        raise ClaimExtractionError("no_json_block_found", "stdout empty")

    # 1. FINAL_ANSWER block (preferred contract path)
    final = _FINAL_ANSWER_RE.search(stdout)
    if final:
        return _decode_json(final.group(1), source="FINAL_ANSWER")

    # 2. Last ```json ... ``` block
    json_blocks = list(_JSON_BLOCK_RE.finditer(stdout))
    if json_blocks:
        return _decode_json(json_blocks[-1].group(1), source="json_fence")

    # 3. Brace-balanced scan from the END (the model's last word
    # is usually the answer; earlier `{}` may be examples or trace).
    obj = _scan_last_json_object(stdout)
    if obj is not None:
        return obj

    raise ClaimExtractionError("no_json_block_found", "no fenced or balanced JSON")


def _decode_json(blob: str, *, source: str) -> dict[str, Any]:
    try:
        parsed = json.loads(blob)
    except json.JSONDecodeError as e:
        raise ClaimExtractionError(
            "json_decode_failed", f"{source}: {e.msg} at pos {e.pos}"
        ) from e
    if not isinstance(parsed, dict):
        raise ClaimExtractionError(
            "json_not_object", f"{source} parsed to {type(parsed).__name__}"
        )
    return parsed


def _scan_last_json_object(stdout: str) -> dict[str, Any] | None:
    """Walk backward to find the last balanced ``{...}`` and try to
    parse it. Returns ``None`` (not raises) when nothing parses.

    Quote-awareness is light: we count brace nesting without
    tracking string literals. JSON strings can contain ``{`` or
    ``}`` which will fool this scanner, but the FINAL_ANSWER and
    json-fence paths above handle the well-formed cases. This is
    purely a last-ditch heuristic for sloppy agent output.
    """
    closes: list[int] = []
    for i, ch in enumerate(stdout):
        if ch == "}":
            closes.append(i)
    while closes:
        end = closes.pop()
        # Find matching open by counting from the right.
        depth = 0
        for j in range(end, -1, -1):
            c = stdout[j]
            if c == "}":
                depth += 1
            elif c == "{":
                depth -= 1
                if depth == 0:
                    candidate = stdout[j : end + 1]
                    try:
                        parsed = json.loads(candidate)
                    except json.JSONDecodeError:
                        break  # try the next `}` from the right
                    if isinstance(parsed, dict):
                        return parsed
                    break
    return None


# --------------------------------------------------------------------------
# Schema validation
# --------------------------------------------------------------------------


def _validate_claim(raw: dict[str, Any]) -> ExtractedClaim:
    missing = [
        k
        for k in ("challenge_id", "culprit_file", "line_range", "failure_mode")
        if k not in raw or raw[k] in (None, "")
    ]
    if missing:
        raise ClaimExtractionError(
            "missing_required_fields", ",".join(missing)
        )

    challenge_id = raw["challenge_id"]
    culprit_file = raw["culprit_file"]
    failure_mode = raw["failure_mode"]
    if not isinstance(challenge_id, str) or not isinstance(culprit_file, str) or not isinstance(failure_mode, str):
        raise ClaimExtractionError("wrong_field_type", "expected string for challenge_id, culprit_file, failure_mode")

    line_range_raw = raw["line_range"]
    if not isinstance(line_range_raw, (list, tuple)) or len(line_range_raw) != 2:
        raise ClaimExtractionError(
            "bad_line_range", f"expected 2-element list/tuple, got {line_range_raw!r}"
        )
    try:
        start = int(line_range_raw[0])
        end = int(line_range_raw[1])
    except (TypeError, ValueError) as e:
        raise ClaimExtractionError(
            "bad_line_range", f"non-int values: {line_range_raw!r}"
        ) from e
    if start > end:
        raise ClaimExtractionError(
            "bad_line_range", f"start {start} > end {end}"
        )

    symbol = raw.get("culprit_symbol")
    if symbol is not None and not isinstance(symbol, str):
        raise ClaimExtractionError(
            "wrong_field_type", "culprit_symbol must be string or null"
        )
    repro = raw.get("repro_input")
    if repro is not None and not isinstance(repro, str):
        raise ClaimExtractionError(
            "wrong_field_type", "repro_input must be string or null"
        )
    explanation = raw.get("explanation")
    if explanation is not None and not isinstance(explanation, str):
        raise ClaimExtractionError(
            "wrong_field_type", "explanation must be string or null"
        )

    return ExtractedClaim(
        challenge_id=challenge_id,
        culprit_file=culprit_file,
        culprit_symbol=symbol,
        line_range=(start, end),
        failure_mode=failure_mode,
        repro_input=repro,
        explanation=explanation,
    )
