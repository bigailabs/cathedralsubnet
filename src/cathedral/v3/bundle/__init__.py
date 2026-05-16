"""Repo bundle: reproducible signed snapshots of a target repo @ commit.

A `RepoBundle` is the unit of "what the miner sees". It contains a flat
file list with BLAKE3 hashes, an aggregate hash, and an ed25519
signature by the validator's signing key. Miners receive the bundle;
validators recreate the exact state and rehash to verify.
"""

from cathedral.v3.bundle.builder import (
    BundleVerification,
    RepoBundle,
    RepoBundleEntry,
    build_bundle,
    materialize_bundle,
    verify_bundle,
)

__all__ = [
    "BundleVerification",
    "RepoBundle",
    "RepoBundleEntry",
    "build_bundle",
    "materialize_bundle",
    "verify_bundle",
]
