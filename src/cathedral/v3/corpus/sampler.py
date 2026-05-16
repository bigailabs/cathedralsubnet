"""Deterministic per-epoch challenge sampler for bug_isolation_v1.

Goal: when N active hotkeys submit in the same epoch and the corpus
has at least N entries, each hotkey gets a distinct challenge_id.
When N exceeds corpus size, collisions are expected and documented.

Uses sha256 for cross-process determinism. Never relies on Python's
salted built-in ``hash()``.
"""

from __future__ import annotations

import hashlib


def _deterministic_shuffle(items: list[str], *, seed: bytes) -> list[str]:
    """Fisher-Yates shuffle driven by a sha256(seed||index) stream.

    Stable across Python versions and processes. Output order depends
    only on ``items`` and ``seed``.
    """
    out = list(items)
    n = len(out)
    for i in range(n - 1, 0, -1):
        digest = hashlib.sha256(seed + i.to_bytes(8, "big")).digest()
        # Use 8 bytes as an unsigned int, mod (i+1).
        j = int.from_bytes(digest[:8], "big") % (i + 1)
        out[i], out[j] = out[j], out[i]
    return out


def sample_challenge_id_for_hotkey(
    *,
    hotkey: str,
    active_hotkeys: list[str],
    corpus_ids: list[str],
    epoch_number: int,
    task_type: str = "bug_isolation_v1",
) -> str:
    """Return the challenge_id assigned to ``hotkey`` for ``epoch_number``.

    Algorithm:
        ordered = sorted(active_hotkeys)
        seed    = sha256(f"{epoch_number}:{task_type}")
        shuffled = deterministic_shuffle(corpus_ids, seed=seed)
        idx     = ordered.index(hotkey)
        return shuffled[idx % len(shuffled)]

    Collisions: if len(active_hotkeys) > len(corpus_ids), multiple
    hotkeys map to the same challenge_id (modulo). This is expected
    and documented; the corpus grows in v3.0.1.
    """
    if not corpus_ids:
        raise ValueError("corpus_ids is empty; no challenges to sample")
    if hotkey not in active_hotkeys:
        raise ValueError(
            f"hotkey {hotkey!r} not in active_hotkeys; sampler is "
            "scoped to the current epoch's active set"
        )

    ordered = sorted(set(active_hotkeys))
    seed = hashlib.sha256(f"{epoch_number}:{task_type}".encode()).digest()
    shuffled = _deterministic_shuffle(list(corpus_ids), seed=seed)
    idx = ordered.index(hotkey)
    return shuffled[idx % len(shuffled)]
