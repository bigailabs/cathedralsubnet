"""Build, materialize, and verify reproducible signed repo bundles.

A bundle binds an explicit file list to the validator's identity:
  - per-file BLAKE3 hash (over the raw file bytes)
  - aggregate BLAKE3 hash over the canonical (sorted) manifest
  - ed25519 signature by the bundle creator over the aggregate hash

Constructors:
  - build_bundle(files) for in-memory content
  - build_bundle_from_dir(root) for a real directory

Verifiers:
  - verify_bundle(bundle, signer_pubkey_hex=...) checks signature + hashes
  - materialize_bundle(bundle, dest) writes the bundle into a workdir
    and re-verifies every byte against its hash before declaring it ok

Bundles are intentionally minimal. They do NOT carry permissions, file
modes, symlinks, or directories-as-entities. A bundle is "this exact
list of byte strings under these exact names". Coding jobs that need
richer state (executable bits, submodules) will compose multiple
bundles.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import blake3
from nacl.exceptions import BadSignatureError
from nacl.signing import VerifyKey
from pydantic import BaseModel, ConfigDict

from cathedral.v3.receipt import ReceiptSigner
from cathedral.v3.types import canonical_json

# Bundle file names must be a relative posix path of safe segments.
# No leading slash, no '..', no empty segment, no shell metacharacters.
_SAFE_SEG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _validate_path(name: str) -> None:
    if not name or name.startswith("/") or "\\" in name:
        raise ValueError(f"unsafe bundle path: {name!r}")
    segs = name.split("/")
    if any(s in ("", ".", "..") for s in segs):
        raise ValueError(f"unsafe bundle path: {name!r}")
    for s in segs:
        if not _SAFE_SEG_RE.match(s):
            raise ValueError(f"unsafe bundle path segment: {s!r} in {name!r}")


class RepoBundleEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    size_bytes: int
    blake3_hex: str  # hash over the raw file bytes


class RepoBundle(BaseModel):
    """A signed snapshot of a flat file list."""

    model_config = ConfigDict(extra="forbid")

    bundle_id: str
    label: str = ""  # human-readable, e.g. "repo@commit-abc123"
    created_at: datetime
    entries: list[RepoBundleEntry]
    contents: dict[str, str]  # path -> raw text content
    aggregate_blake3_hex: str
    signature_scheme: str = "ed25519"
    signer_pubkey_hex: str
    signature_hex: str

    def canonical_manifest(self) -> bytes:
        """The bytes the signature is computed over.

        Includes path, size, hash for every entry, plus the bundle_id
        and label. Excludes signature fields (those would be circular)
        and contents (the per-file hashes already commit to them).
        """
        manifest = {
            "bundle_id": self.bundle_id,
            "label": self.label,
            "entries": [
                {"path": e.path, "size_bytes": e.size_bytes, "blake3_hex": e.blake3_hex}
                for e in sorted(self.entries, key=lambda x: x.path)
            ],
        }
        return canonical_json(manifest)

    def recompute_aggregate(self) -> str:
        return blake3.blake3(self.canonical_manifest()).hexdigest()


@dataclass
class BundleVerification:
    ok: bool
    reason: str | None = None
    per_file_ok: bool = True
    aggregate_ok: bool = True
    signature_ok: bool = True


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------


def build_bundle(
    files: dict[str, str],
    *,
    signer: ReceiptSigner,
    bundle_id: str | None = None,
    label: str = "",
) -> RepoBundle:
    """Build and sign a bundle from an in-memory file map."""
    if not files:
        raise ValueError("cannot build empty bundle")

    entries: list[RepoBundleEntry] = []
    contents: dict[str, str] = {}
    for path, body in files.items():
        _validate_path(path)
        raw = body.encode("utf-8")
        entries.append(
            RepoBundleEntry(
                path=path,
                size_bytes=len(raw),
                blake3_hex=blake3.blake3(raw).hexdigest(),
            )
        )
        contents[path] = body

    entries.sort(key=lambda e: e.path)
    bid = bundle_id or _derive_bundle_id(entries)
    created_at = datetime.now(UTC)

    bundle = RepoBundle(
        bundle_id=bid,
        label=label,
        created_at=created_at,
        entries=entries,
        contents=contents,
        aggregate_blake3_hex="",  # filled next
        signer_pubkey_hex=signer.public_hex,
        signature_hex="",
    )
    aggregate = bundle.recompute_aggregate()
    sig = signer.sign_bytes(bundle.canonical_manifest())
    return bundle.model_copy(update={"aggregate_blake3_hex": aggregate, "signature_hex": sig})


def build_bundle_from_dir(
    root: Path,
    *,
    signer: ReceiptSigner,
    bundle_id: str | None = None,
    label: str = "",
    include_globs: list[str] | None = None,
) -> RepoBundle:
    root = Path(root)
    files: dict[str, str] = {}
    patterns = include_globs or ["**/*"]
    seen: set[Path] = set()
    for pat in patterns:
        for p in root.glob(pat):
            if not p.is_file() or p in seen:
                continue
            rel = p.relative_to(root).as_posix()
            try:
                _validate_path(rel)
            except ValueError:
                continue
            files[rel] = p.read_text()
            seen.add(p)
    return build_bundle(files, signer=signer, bundle_id=bundle_id, label=label)


# ---------------------------------------------------------------------------
# verify
# ---------------------------------------------------------------------------


def verify_bundle(
    bundle: RepoBundle,
    *,
    expected_pubkey_hex: str | None = None,
) -> BundleVerification:
    """Check per-file hashes, aggregate hash, and signature.

    If `expected_pubkey_hex` is set, also fails when the bundle was
    signed by a different key.
    """
    for entry in bundle.entries:
        content = bundle.contents.get(entry.path)
        if content is None:
            return BundleVerification(
                ok=False,
                reason=f"missing content for entry: {entry.path}",
                per_file_ok=False,
            )
        recomputed = blake3.blake3(content.encode("utf-8")).hexdigest()
        if recomputed != entry.blake3_hex:
            return BundleVerification(
                ok=False,
                reason=f"per-file hash mismatch on {entry.path}",
                per_file_ok=False,
            )

    aggregate = bundle.recompute_aggregate()
    if aggregate != bundle.aggregate_blake3_hex:
        return BundleVerification(
            ok=False,
            reason="aggregate hash mismatch",
            aggregate_ok=False,
        )

    if expected_pubkey_hex and bundle.signer_pubkey_hex != expected_pubkey_hex:
        return BundleVerification(
            ok=False,
            reason="signer pubkey mismatch",
            signature_ok=False,
        )

    try:
        vk = VerifyKey(bytes.fromhex(bundle.signer_pubkey_hex))
        vk.verify(bundle.canonical_manifest(), bytes.fromhex(bundle.signature_hex))
    except (BadSignatureError, ValueError) as e:
        return BundleVerification(
            ok=False,
            reason=f"signature verification failed: {e}",
            signature_ok=False,
        )

    return BundleVerification(ok=True)


def materialize_bundle(bundle: RepoBundle, dest: Path) -> BundleVerification:
    """Write the bundle into `dest`, then re-verify every byte.

    Refuses to write if `dest` exists and is not an empty directory.
    Returns the verification result; on failure, leaves whatever has
    been written so the caller can inspect.
    """
    dest = Path(dest)
    if dest.exists() and any(dest.iterdir()):
        return BundleVerification(ok=False, reason="dest exists and is not empty")
    dest.mkdir(parents=True, exist_ok=True)
    v = verify_bundle(bundle)
    if not v.ok:
        return v
    for entry in bundle.entries:
        out = dest / entry.path
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(bundle.contents[entry.path])
    return v


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _derive_bundle_id(entries: list[RepoBundleEntry]) -> str:
    h = blake3.blake3()
    for e in entries:
        h.update(f"{e.path}:{e.blake3_hex}:{e.size_bytes}\n".encode())
    return f"bundle_{h.hexdigest()[:16]}"


__all__ = [
    "BundleVerification",
    "RepoBundle",
    "RepoBundleEntry",
    "build_bundle",
    "build_bundle_from_dir",
    "materialize_bundle",
    "verify_bundle",
]
