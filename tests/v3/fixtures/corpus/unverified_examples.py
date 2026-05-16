"""Unverified bug_isolation_v1 example rows.

**NON-PRODUCTION. PARSER + SCORER UNIT FIXTURES ONLY.**

The rows below have plausible structure and reference real Python
repos and real bug *classes*, but the commit SHAs and exact line
ranges have not been verified against upstream history. They exist
to exercise the schema, scorer, and sampler paths in unit tests
without touching the production pilot corpus.

These rows MUST NOT be imported by any module under
``src/cathedral/`` and MUST NOT be merged into
``src/cathedral/v3/corpus/seed_pilot.py``.

When you verify one of these (open the source_url, confirm the
parent-of-fix SHA, pin the line range from the fix diff, choose
keywords from the diff body), MOVE it to ``seed_pilot.py`` and
delete it from this file.
"""

from __future__ import annotations

from cathedral.v3.corpus.schema import ChallengeRow

UNVERIFIED_EXAMPLES: tuple[ChallengeRow, ...] = (
    # click: mutable default in Option.__init__ (real bug class, SHA unverified)
    ChallengeRow(
        id="UNVERIFIED_click_mutable_default_envvar",
        repo="https://github.com/pallets/click",
        commit="d0af32d4f7c0c2d9c1e3c08b8d5e5d0e0f0c0a0b",
        issue_text=(
            "Click Option accepts a mutable list as the default value "
            "and the same list is shared across all invocations of "
            "commands that use that option."
        ),
        culprit_file="src/click/core.py",
        culprit_symbol="Option.__init__",
        line_range=(2200, 2280),
        required_failure_keywords=("mutable", "default", "shared"),
        difficulty="medium",
        bucket="mutable_default",
        source_url="https://github.com/pallets/click/pull/1556",
    ),
    # cryptography: off-by-one in PKCS7 unpadding (real bug class, SHA unverified)
    ChallengeRow(
        id="UNVERIFIED_cryptography_padding_off_by_one",
        repo="https://github.com/pyca/cryptography",
        commit="7a3c2f9d2c8e1b5d4a6f8e9c0d1b2a3c4e5f6789",
        issue_text=(
            "PKCS7 unpadding can read one byte past the end of the "
            "buffer when the padding byte value equals the block size."
        ),
        culprit_file="src/cryptography/hazmat/primitives/padding.py",
        culprit_symbol="PKCS7.unpadder",
        line_range=(80, 140),
        required_failure_keywords=("padding", "off-by-one", "buffer"),
        difficulty="hard",
        bucket="off_by_one",
        source_url="https://github.com/pyca/cryptography/blob/main/src/cryptography/hazmat/primitives/padding.py",
    ),
    # fastapi: dependency override leak across requests (real issue, SHA unverified)
    ChallengeRow(
        id="UNVERIFIED_fastapi_dependency_override_leak",
        repo="https://github.com/tiangolo/fastapi",
        commit="a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0",
        issue_text=(
            "When an application overrides a dependency during testing, "
            "the override persists into the next request because the "
            "override dict is module-level and not cleared between "
            "requests."
        ),
        culprit_file="fastapi/dependencies/utils.py",
        culprit_symbol="solve_dependencies",
        line_range=(420, 510),
        required_failure_keywords=("override", "dependency", "leak"),
        difficulty="medium",
        bucket="state_leak",
        source_url="https://github.com/tiangolo/fastapi/issues/5688",
    ),
    # pydantic: validator skipped on default (file-level, SHA unverified)
    ChallengeRow(
        id="UNVERIFIED_pydantic_validator_skips_default",
        repo="https://github.com/pydantic/pydantic",
        commit="b5d4c3a2e1f0d9c8b7a6f5e4d3c2b1a0e9d8c7b6",
        issue_text=(
            "A field validator does not run when the field uses its "
            "default value, only when the value is explicitly supplied. "
            "This contradicts the documentation."
        ),
        culprit_file="pydantic/fields.py",
        culprit_symbol=None,
        line_range=(100, 180),
        required_failure_keywords=("validator", "default", "skip"),
        difficulty="medium",
        bucket="config_default",
        source_url="https://github.com/pydantic/pydantic/blob/main/pydantic/fields.py",
    ),
)
