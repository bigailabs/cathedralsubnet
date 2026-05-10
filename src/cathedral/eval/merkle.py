"""Merkle tree helpers for eval-run anchoring (CONTRACTS.md §4.5).

Algorithm:
- leaf = blake3(":".join([id, output_card_hash, str(weighted_score),
                          cathedral_signature]))
- sort leaves by eval_run_id ascending
- if odd count, duplicate last (Bitcoin-style)
- parent = blake3(left_hex + right_hex)  # ASCII hex concatenation
- root = single remaining hash, lowercase hex

Re-exports the implementations from `cathedral.publisher.merkle` so the
public-import location matches the contract reference exactly.
"""

from __future__ import annotations

from cathedral.chain.anchor import encode_anchor_payload
from cathedral.publisher.merkle import (
    close_epoch,
    epoch_for,
    epoch_window,
    merkle_leaf,
    merkle_root,
    previous_epoch,
)

# Aliases tested by tests/v1/test_merkle_anchor.py.
compute_merkle_root = merkle_root
build_root = merkle_root
compute_leaf = merkle_leaf
leaf_hash = merkle_leaf
encode_commit = encode_anchor_payload

__all__ = [
    "build_root",
    "close_epoch",
    "compute_leaf",
    "compute_merkle_root",
    "encode_commit",
    "epoch_for",
    "epoch_window",
    "leaf_hash",
    "merkle_leaf",
    "merkle_root",
    "previous_epoch",
]
