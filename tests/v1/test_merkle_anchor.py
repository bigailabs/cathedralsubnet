"""Merkle anchor — CONTRACTS.md §4.5, §4.6, §1.13.

Algorithm (§4.5):
- leaf_i = blake3(id ":" output_card_hash ":" str(weighted_score) ":" sig).hex()
- sort leaves by eval_run_id ascending (lex on UUID string)
- pad odd to even by duplicating the last leaf
- parent = blake3(left_hex_lower + right_hex_lower).hex()  -- ASCII concat of hex strings
- root = single remaining hash, lowercase hex

On-chain commit (§4.6):
- bytes = b"cath:v1:" + epoch_be(4) + bytes.fromhex(merkle_root)  -- 44 bytes total

We test against a reference implementation derived from the contract.
If the implementer exposes equivalents, we cross-check.
"""

from __future__ import annotations

import importlib
import struct
from collections.abc import Callable

import blake3
import pytest

# --------------------------------------------------------------------------
# Reference implementation — directly transcribed from §4.5
# --------------------------------------------------------------------------


def reference_merkle_leaf(run: dict) -> str:
    parts = [
        run["id"],
        run["output_card_hash"],
        str(run["weighted_score"]),
        run["cathedral_signature"],
    ]
    return blake3.blake3(":".join(parts).encode("utf-8")).hexdigest()


def reference_merkle_root(leaves_hex_sorted: list[str]) -> str:
    if not leaves_hex_sorted:
        return blake3.blake3(b"").hexdigest()
    layer = list(leaves_hex_sorted)
    while len(layer) > 1:
        if len(layer) % 2 == 1:
            layer = [*layer, layer[-1]]
        layer = [
            blake3.blake3((a + b).encode("utf-8")).hexdigest()
            for a, b in zip(layer[::2], layer[1::2], strict=True)
        ]
    return layer[0]


def reference_on_chain_commit(epoch: int, merkle_root_hex: str) -> bytes:
    """§4.6 — `b"cath:v1:" + epoch_be(4) + bytes.fromhex(root)` (44 bytes)."""
    return b"cath:v1:" + struct.pack(">I", epoch) + bytes.fromhex(merkle_root_hex)


# --------------------------------------------------------------------------
# Reference behavior tests (always run)
# --------------------------------------------------------------------------


def test_single_leaf_root_equals_leaf():
    """§4.5 — 1 leaf → root = leaf (no further hashing layers)."""
    leaf = blake3.blake3(b"only").hexdigest()
    root = reference_merkle_root([leaf])
    assert root == leaf, "§4.5: single-leaf tree's root must equal that leaf"


def test_three_leaves_odd_padding_is_deterministic():
    """§4.5 — 3 leaves → odd-leaf duplicated, root deterministic."""
    leaves = sorted(
        [
            blake3.blake3(b"a").hexdigest(),
            blake3.blake3(b"b").hexdigest(),
            blake3.blake3(b"c").hexdigest(),
        ]
    )
    root_a = reference_merkle_root(leaves)
    root_b = reference_merkle_root(leaves)
    assert root_a == root_b
    assert len(root_a) == 64 and root_a == root_a.lower()


def test_root_is_lowercase_hex_64_chars():
    """§9 lock #4 — root is lowercase hex, 64 chars (no 0x, no uppercase)."""
    root = reference_merkle_root(
        [blake3.blake3(b"x").hexdigest(), blake3.blake3(b"y").hexdigest()]
    )
    assert len(root) == 64
    assert root == root.lower()
    assert not root.startswith("0x")


def test_empty_tree_returns_blake3_of_empty():
    """§4.5 — empty tree falls back to blake3(b"") (matches reference)."""
    root = reference_merkle_root([])
    assert root == blake3.blake3(b"").hexdigest()


def test_leaf_encoding_uses_colon_join():
    """§4.5 — leaf = blake3(id ":" output_card_hash ":" str(score) ":" sig)."""
    run = {
        "id": "11111111-1111-1111-1111-111111111111",
        "output_card_hash": "aa" * 32,
        "weighted_score": 0.5,
        "cathedral_signature": "fakesig==",
    }
    leaf = reference_merkle_leaf(run)
    expected = blake3.blake3(
        b"11111111-1111-1111-1111-111111111111:" + b"aa" * 32 + b":0.5:fakesig=="
    ).hexdigest()
    assert leaf == expected


def test_root_construction_with_known_pair():
    """§4.5 — parent = blake3(left_hex + right_hex) ASCII concat."""
    a = blake3.blake3(b"alpha").hexdigest()
    b = blake3.blake3(b"beta").hexdigest()
    leaves = sorted([a, b])
    expected = blake3.blake3(
        (leaves[0] + leaves[1]).encode("utf-8")
    ).hexdigest()
    assert reference_merkle_root(leaves) == expected


def test_proof_path_can_verify_each_leaf():
    """§4.5 — every leaf can be proven against the root via its sibling path.

    We don't have a contract-defined proof format so we verify the
    reference impl's invariant: rebuilding the root with the leaf and its
    siblings reproduces the root.
    """
    leaves = sorted([blake3.blake3(f"x{i}".encode()).hexdigest() for i in range(5)])
    root = reference_merkle_root(leaves)

    for target in leaves:
        # Brute force: rebuild the layer the same way the reference does
        # and confirm the root matches.
        layer = list(leaves)
        idx = layer.index(target)
        proof: list[tuple[str, str]] = []  # (sibling_hex, side)
        while len(layer) > 1:
            if len(layer) % 2 == 1:
                layer = [*layer, layer[-1]]
            sibling_idx = idx + 1 if idx % 2 == 0 else idx - 1
            sibling = layer[sibling_idx]
            side = "right" if idx % 2 == 0 else "left"
            proof.append((sibling, side))
            new_layer = []
            for i in range(0, len(layer), 2):
                left, right = layer[i], layer[i + 1]
                new_layer.append(
                    blake3.blake3((left + right).encode("utf-8")).hexdigest()
                )
            layer = new_layer
            idx //= 2

        # Walk the proof from the leaf back up.
        current = target
        for sibling, side in proof:
            if side == "right":
                current = blake3.blake3(
                    (current + sibling).encode("utf-8")
                ).hexdigest()
            else:
                current = blake3.blake3(
                    (sibling + current).encode("utf-8")
                ).hexdigest()
        assert current == root, (
            f"§4.5 proof verification failed for leaf {target[:8]}.. "
            f"reconstructed root {current} != actual root {root}"
        )


# --------------------------------------------------------------------------
# On-chain commit format (§4.6)
# --------------------------------------------------------------------------


def test_on_chain_commit_is_44_bytes():
    """§4.6 — exactly `b'cath:v1:' (8) + epoch (4) + root (32)` = 44 bytes."""
    root = "ab" * 32
    blob = reference_on_chain_commit(epoch=42, merkle_root_hex=root)
    assert len(blob) == 44, f"§4.6: commit blob must be 44 bytes; got {len(blob)}"
    assert blob.startswith(b"cath:v1:"), "§4.6: commit must start with b'cath:v1:'"
    # Epoch is 4 bytes big-endian.
    epoch_bytes = blob[8:12]
    assert struct.unpack(">I", epoch_bytes)[0] == 42, (
        "§4.6: epoch must be encoded as 4-byte big-endian unsigned int"
    )
    assert blob[12:] == bytes.fromhex(root), "§4.6: tail is raw bytes of merkle_root hex"


def test_on_chain_commit_roundtrips_root():
    """§4.6 — commit blob can be decoded back into (epoch, root)."""
    root = blake3.blake3(b"some root").hexdigest()
    blob = reference_on_chain_commit(epoch=2026, merkle_root_hex=root)
    assert blob[:8] == b"cath:v1:"
    decoded_epoch = struct.unpack(">I", blob[8:12])[0]
    decoded_root = blob[12:].hex()
    assert decoded_epoch == 2026
    assert decoded_root == root


# --------------------------------------------------------------------------
# Cross-check with implementer's helpers (when present)
# --------------------------------------------------------------------------


def _find_callable(module_path: str, attr_candidates: tuple[str, ...]) -> Callable | None:
    try:
        mod = importlib.import_module(module_path)
    except Exception:
        return None
    for attr in attr_candidates:
        fn = getattr(mod, attr, None)
        if callable(fn):
            return fn
    return None


def test_implementer_merkle_root_matches_reference():
    """§4.5 — implementer's helper must reproduce the reference root."""
    fn = _find_callable(
        "cathedral.eval.merkle",
        ("merkle_root", "compute_merkle_root", "build_root"),
    ) or _find_callable(
        "cathedral.merkle", ("merkle_root", "compute_merkle_root", "build_root")
    ) or _find_callable(
        "cathedral.chain.merkle", ("merkle_root", "compute_merkle_root")
    )
    if fn is None:
        pytest.skip(
            "merkle_root helper not exposed yet — implementer should publish "
            "cathedral.eval.merkle.merkle_root per CONTRACTS.md §4.5"
        )
    leaves = sorted([blake3.blake3(f"v{i}".encode()).hexdigest() for i in range(7)])
    expected = reference_merkle_root(leaves)
    got = fn(leaves)
    assert got == expected, (
        f"§4.5: implementer's merkle_root drifted from contract reference\n"
        f"  expected: {expected}\n"
        f"  got:      {got}"
    )


def test_implementer_leaf_hash_matches_reference():
    """§4.5 — implementer's leaf helper reproduces the reference leaf."""
    fn = _find_callable(
        "cathedral.eval.merkle", ("merkle_leaf", "compute_leaf", "leaf_hash")
    ) or _find_callable(
        "cathedral.merkle", ("merkle_leaf", "leaf_hash")
    )
    if fn is None:
        pytest.skip(
            "merkle_leaf helper not exposed yet — implementer should publish "
            "cathedral.eval.merkle.merkle_leaf per CONTRACTS.md §4.5"
        )
    run = {
        "id": "00000000-0000-0000-0000-000000000001",
        "output_card_hash": "aa",
        "weighted_score": 0.5,
        "cathedral_signature": "sig1",
    }
    expected = reference_merkle_leaf(run)
    got = fn(run)
    assert got == expected, (
        f"§4.5: implementer's merkle_leaf drifted from contract reference\n"
        f"  expected: {expected}\n"
        f"  got:      {got}"
    )


def test_implementer_on_chain_commit_format():
    """§4.6 — implementer's commit serializer matches the contract."""
    fn = _find_callable(
        "cathedral.chain.anchor", ("encode_commit", "build_commit_payload")
    ) or _find_callable(
        "cathedral.eval.merkle", ("encode_commit",)
    ) or _find_callable(
        "cathedral.chain.merkle", ("encode_commit",)
    )
    if fn is None:
        pytest.skip(
            "on-chain commit encoder not exposed yet — implementer should "
            "publish cathedral.chain.anchor.encode_commit per CONTRACTS.md §4.6"
        )
    root = blake3.blake3(b"contract").hexdigest()
    expected = reference_on_chain_commit(epoch=7, merkle_root_hex=root)
    got = fn(7, root)
    assert got == expected, (
        f"§4.6: implementer's on-chain commit blob drifted\n"
        f"  expected: {expected.hex()}\n"
        f"  got:      {got.hex()}"
    )
