"""Pre-eval similarity check (CONTRACTS.md Section 7.1).

Run synchronously inside the `/v1/agents/submit` handler AFTER bundle
hash computation, BEFORE marking `queued`.

All inputs are derivable from the public-surface fields of a submission;
no bundle decryption is required (NCD on bundle bytes is deferred to v2
because it can't run inside a synchronous request handler).
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import aiosqlite
import blake3

from cathedral.publisher import repository

_DISPLAY_NAME_FUZZY_RATIO_THRESHOLD = 0.85
_DISPLAY_NAME_FUZZY_WINDOW_DAYS = 7
_BUNDLE_SIZE_BUCKET_BYTES = 1024


class SimilarityRejection(Exception):
    """Pre-eval similarity check rejected the submission. Map to 409."""


@dataclass(frozen=True)
class SimilarityResult:
    metadata_fingerprint: str
    display_name_norm: str


def normalize_display_name(name: str) -> str:
    """NFKC-normalize, lowercase, collapse whitespace."""
    n = unicodedata.normalize("NFKC", name).strip().lower()
    return " ".join(n.split())


def metadata_fingerprint(*, display_name: str, bundle_size_bytes: int) -> str:
    """`blake3(display_name_norm | bundle_size_bucket)` per Section 7.1.

    The bucket coarsens to 1 KiB so trivially-resized bundles still
    cluster. Two miners arriving with the same display name and a
    near-identical bundle size collide here.
    """
    norm = normalize_display_name(display_name)
    bucket = bundle_size_bytes // _BUNDLE_SIZE_BUCKET_BYTES
    return blake3.blake3(f"{norm}|{bucket}".encode()).hexdigest()


def levenshtein_ratio(a: str, b: str) -> float:
    """Pure-python Levenshtein ratio (1.0 == identical, 0.0 == disjoint).

    Bounded by the longer string. Implementation is O(n*m) — fine for
    display names ≤ 64 chars.
    """
    if a == b:
        return 1.0
    if not a or not b:
        return 0.0
    n, m = len(a), len(b)
    if n < m:
        a, b = b, a
        n, m = m, n
    prev = list(range(m + 1))
    for i in range(1, n + 1):
        curr = [i] + [0] * m
        for j in range(1, m + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            curr[j] = min(
                curr[j - 1] + 1,
                prev[j] + 1,
                prev[j - 1] + cost,
            )
        prev = curr
    distance = prev[m]
    return 1.0 - (distance / max(n, m))


async def run_similarity_check(
    conn: aiosqlite.Connection,
    *,
    miner_hotkey: str,
    card_id: str,
    display_name: str,
    bundle_hash: str,
    bundle_size_bytes: int,
) -> SimilarityResult:
    """Apply checks 7.1.1 → 7.1.4. Raise on rejection.

    Returns the computed `metadata_fingerprint` so the caller doesn't
    have to re-derive it before insertion.

    Note: check 7.1.2 (same hotkey + same bundle) is enforced by the
    `idx_agent_unique` UNIQUE index — sqlite raises IntegrityError on
    insert and the submit handler maps to 409.
    """
    # Check 7.1.1 — exact bundle hash collision across hotkeys.
    existing_bundle = await repository.find_existing_bundle_hash(
        conn, card_id, bundle_hash
    )
    if existing_bundle is not None:
        # Distinguish same-hotkey (covered by UNIQUE index) from
        # cross-hotkey duplicate so the operator dashboard can tell.
        if existing_bundle["miner_hotkey"] == miner_hotkey:
            raise SimilarityRejection("duplicate submission")
        raise SimilarityRejection("exact bundle duplicate")

    # Check 7.1.3 — fuzzy display-name collision in the last 7 days.
    norm = normalize_display_name(display_name)
    since = datetime.now(UTC) - timedelta(days=_DISPLAY_NAME_FUZZY_WINDOW_DAYS)
    recent = await repository.list_recent_display_names(conn, card_id, since)
    for _, other_name in recent:
        other_norm = normalize_display_name(other_name)
        if other_norm == norm:
            # Trigger the same rejection path the fuzzy check uses; the
            # exact collision is a degenerate case of the fuzzy one.
            raise SimilarityRejection(f"display name too similar to {other_name!r}")
        if levenshtein_ratio(norm, other_norm) >= _DISPLAY_NAME_FUZZY_RATIO_THRESHOLD:
            raise SimilarityRejection(f"display name too similar to {other_name!r}")

    fingerprint = metadata_fingerprint(
        display_name=display_name, bundle_size_bytes=bundle_size_bytes
    )

    # Check 7.1.4 — fingerprint collision from a different hotkey.
    other = await repository.find_metadata_fingerprint_collision(
        conn, card_id, fingerprint, miner_hotkey
    )
    if other is not None:
        raise SimilarityRejection("metadata fingerprint duplicate")

    return SimilarityResult(metadata_fingerprint=fingerprint, display_name_norm=norm)
