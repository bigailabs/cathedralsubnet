"""Prompt contracts for v3 benchmark capabilities."""

from __future__ import annotations

import json

from cathedral.v3.corpus.schema import ChallengeRow


def build_bug_isolation_prompt(challenge: ChallengeRow) -> str:
    """Build the miner-facing prompt for bug_isolation_v1.

    The hidden oracle fields are intentionally absent. The miner sees
    only repo, broken commit, issue text, and the response contract.
    """
    public = challenge.public_view()
    payload = json.dumps(public, sort_keys=True, indent=2)
    return (
        "Capability: bug_isolation_v1\n\n"
        "You are debugging a public Python repository at a specific broken commit. "
        "Do not run tests supplied by Cathedral. Inspect the repository and identify "
        "where the described bug lives.\n\n"
        "Challenge:\n"
        f"{payload}\n\n"
        "Return exactly one fenced FINAL_ANSWER JSON block with this shape:\n"
        "```FINAL_ANSWER\n"
        "{\n"
        '  "challenge_id": "<challenge_id from the prompt>",\n'
        '  "culprit_file": "path/to/file.py",\n'
        '  "culprit_symbol": "function_or_method_name_or_null",\n'
        '  "line_range": [start_line, end_line],\n'
        '  "failure_mode": "short explanation of the root cause"\n'
        "}\n"
        "```\n\n"
        "Use only the repository state at the requested commit. Do not include prose "
        "outside the FINAL_ANSWER block."
    )


def build_bug_isolation_repair_prompt(challenge_id: str) -> str:
    """One-shot repair prompt when the first response had no parseable JSON."""
    return (
        "Your previous response did not include a parseable FINAL_ANSWER JSON block. "
        "Return only this fenced block now, with no prose:\n\n"
        "```FINAL_ANSWER\n"
        "{\n"
        f'  "challenge_id": "{challenge_id}",\n'
        '  "culprit_file": "path/to/file.py",\n'
        '  "culprit_symbol": "function_or_method_name_or_null",\n'
        '  "line_range": [start_line, end_line],\n'
        '  "failure_mode": "short explanation of the root cause"\n'
        "}\n"
        "```"
    )


__all__ = ["build_bug_isolation_prompt", "build_bug_isolation_repair_prompt"]
