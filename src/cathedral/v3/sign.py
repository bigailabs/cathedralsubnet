"""Build and sign a v3 wire payload for bug_isolation_v1 results.

Pure function over (dispatch result, signer). Caller is responsible
for the env-flag gate (``CATHEDRAL_V3_FEED_ENABLED``) and for
persisting the row to the publisher DB. This module only:

  - Assembles the signed subset (the v3 keyset).
  - Canonicalizes via cathedral.v1_types.canonical_json.
  - Signs with the supplied EvalSigner.
  - Returns a wire-shape dict ready to serve from
    ``/v1/leaderboard/recent`` (or a sibling endpoint), including
    the unsigned envelope fields validators tolerate.

The signed keyset is locked in
``cathedral.eval.v2_payload._SIGNED_KEYS_BY_VERSION[3]`` and the
validator-side mirror in
``cathedral.validator.pull_loop._SIGNED_KEYS_BY_VERSION[3]``. Tests
in ``tests/v3/test_sign_payload_v3.py`` pin both.

Public-feed envelope policy:
  - ``challenge_id`` is signed (validators need it to verify which
    corpus row was scored), but the public read surface should
    expose only a *hash* of the challenge_id, not the raw value.
    This slows Discord-style answer-sharing. The hashed form lives
    in ``challenge_id_public`` alongside the raw signed value.
    Surfaces that serve to miners/site should drop the raw field
    and keep only the hash; validator-facing endpoints keep both.
"""

from __future__ import annotations

import base64
import hashlib
from typing import Any

from cathedral.v1_types import canonical_json
from cathedral.v3.dispatch import DispatchResult

# v3 signed keyset, locked locally to keep `cathedral.v3` independent
# of the publisher's eval package (which has a documented circular
# import with the publisher app). The cross-module byte-equality
# with `cathedral.eval.v2_payload._SIGNED_KEYS_BY_VERSION[3]` and
# `cathedral.validator.pull_loop._SIGNED_KEYS_BY_VERSION[3]` is
# enforced by tests/v3/test_sign_payload_v3.py.
_V3_SIGNED_KEYS: frozenset[str] = frozenset(
    {
        "id",
        "agent_id",
        "agent_display_name",
        "miner_hotkey",
        "task_type",
        "challenge_id",
        "challenge_id_public",
        "weighted_score",
        "score_parts",
        "claim",
        "ran_at",
    }
)

# Hash truncation for the public challenge_id. 12 hex chars ~= 48 bits,
# enough to make collisions vanishingly unlikely across a small
# corpus while keeping the public id short for site/UI use.
_CHALLENGE_ID_HASH_PREFIX_LEN: int = 12


def hash_challenge_id(challenge_id: str, *, epoch_salt: str | None = None) -> str:
    """Public-feed hash of the challenge_id.

    **WARNING: TEMPORARY, NON-PRODUCTION WHEN ``epoch_salt`` IS NONE.**

    When ``epoch_salt`` is supplied, the hash is
    ``sha256(epoch_salt || ":" || challenge_id)`` truncated to a
    short hex prefix. This rotates the ``raw -> public`` mapping
    per epoch so two miners cannot share answers by quoting the
    public id from a previous epoch.

    When ``epoch_salt`` is None (the framework PR default), the
    hash is a salt-free ``sha256(challenge_id)`` prefix. That is
    deterministic across hosts and trivially reversible by anyone
    who can enumerate plausible raw challenge_ids. It is fine for
    unit tests and for the scaffolding PR (where no challenge_ids
    are ever served), but MUST be replaced with a salted hash
    before live exposure (``CATHEDRAL_V3_FEED_ENABLED=true``).

    Live publisher code must always pass ``epoch_salt`` once the
    feed flips; this is tracked in
    ``src/cathedral/v3/corpus/CORPUS_TODO.md``.
    """
    body = challenge_id if epoch_salt is None else f"{epoch_salt}:{challenge_id}"
    digest = hashlib.sha256(body.encode()).hexdigest()
    return digest[:_CHALLENGE_ID_HASH_PREFIX_LEN]


def build_signed_v3_bug_isolation_row(
    *,
    eval_run_id: str,
    submission_id: str,
    agent_display_name: str,
    miner_hotkey: str,
    challenge_id: str,
    dispatch_result: DispatchResult,
    ran_at_iso: str,
    signer: Any,
    failure_reason: str | None = None,
    shadow_metrics: dict[str, Any] | None = None,
    epoch_salt: str | None = None,
) -> dict[str, Any]:
    """Assemble + sign a v3 wire row.

    ``signer`` is the existing ``cathedral.eval.scoring_pipeline.EvalSigner``
    instance held by the publisher. Typed as ``Any`` so this module
    avoids the scoring_pipeline import (which pulls the publisher
    package and triggers the circular import we documented in
    tests/v3/test_sign_payload_v3.py).

    Failure rows (parse error, challenge_id mismatch, etc.) sign a
    zero-score payload with the partial claim if we have it. The
    publisher should still sign+serve so the miner has a verifiable
    failure record.

    ``epoch_salt`` is forwarded to ``hash_challenge_id`` to produce
    ``challenge_id_public``. Default ``None`` keeps the unsalted
    framework-PR behavior so unit tests stay deterministic. The
    follow-up PR that enables ``CATHEDRAL_V3_FEED_ENABLED`` must
    pass a real per-epoch salt (for example ``f"epoch_{epoch_number}"``)
    so the public id rotates per epoch and cannot be used for
    cross-miner answer-sharing.
    """
    if (
        dispatch_result.ok
        and dispatch_result.score is not None
        and dispatch_result.claim is not None
    ):
        weighted = dispatch_result.score.weighted_score
        score_parts = dispatch_result.score.to_parts_dict()
        claim_dict = dispatch_result.claim.to_dict()
    else:
        weighted = 0.0
        score_parts = {
            "culprit_file": 0.0,
            "culprit_symbol": 0.0,
            "line_range": 0.0,
            "failure_mode": 0.0,
        }
        claim_dict = (
            dispatch_result.claim.to_dict()
            if dispatch_result.claim is not None
            else {"challenge_id": challenge_id, "_failure_reason": failure_reason}
        )

    challenge_id_public = hash_challenge_id(challenge_id, epoch_salt=epoch_salt)
    signed_subset = {
        "id": eval_run_id,
        "agent_id": submission_id,
        "agent_display_name": agent_display_name,
        "miner_hotkey": miner_hotkey,
        "task_type": "bug_isolation_v1",
        "challenge_id": challenge_id,
        "challenge_id_public": challenge_id_public,
        "weighted_score": weighted,
        "score_parts": score_parts,
        "claim": claim_dict,
        "ran_at": ran_at_iso,
    }

    # Sanity: every key in the signed subset must be in the v3 keyset.
    # Catches typos before they break verification at runtime.
    extra = set(signed_subset.keys()) - set(_V3_SIGNED_KEYS)
    missing = set(_V3_SIGNED_KEYS) - set(signed_subset.keys())
    if extra or missing:
        raise RuntimeError(
            f"v3 signed subset diverged from keyset: extra={sorted(extra)} "
            f"missing={sorted(missing)}"
        )

    payload_bytes = canonical_json(signed_subset)
    # TODO(#issue): private-attr access on EvalSigner. The existing
    # contract exposes only `.sign(dict)` which canonicalizes
    # internally; we need raw-bytes signing to keep canonicalization
    # consistent across modules. Move to a public `.sign_bytes(bytes)`
    # method on EvalSigner in a small refactor PR.
    sig_b64 = base64.b64encode(signer._sk.sign(payload_bytes)).decode("ascii")

    row = dict(signed_subset)
    row["cathedral_signature"] = sig_b64
    row["eval_output_schema_version"] = 3
    # Envelope (unsigned). Validators tolerate; site uses for UX.
    # When epoch_salt is None (framework PR), the hash is stable and
    # reversible; production must pass a real per-epoch salt.
    row["challenge_id_public"] = challenge_id_public
    if shadow_metrics is not None:
        row["shadow_metrics"] = shadow_metrics
    if failure_reason is not None:
        row["failure_reason"] = failure_reason
    return row


def public_feed_view(row: dict[str, Any]) -> dict[str, Any]:
    """Project a signed v3 row down to the shape served on public
    feeds (miner-visible /v1/leaderboard/recent equivalent).

    Strips the raw ``challenge_id`` so miners can't share answers
    by challenge_id. ``challenge_id_public`` (the hash) stays.
    Validators that need the raw id for verification call a
    separate validator-facing endpoint (out of scope for this PR;
    feed flag stays off in v3.0).
    """
    out = dict(row)
    out.pop("challenge_id", None)
    return out


__all__ = [
    "build_signed_v3_bug_isolation_row",
    "hash_challenge_id",
    "public_feed_view",
]
