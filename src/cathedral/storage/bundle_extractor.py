r"""Safely extract Hermes profile zip bundles to ephemeral disk.

Adversarial threat model (CONTRACTS.md cross-cutting concern):

- Path traversal via ZIP entry names (`../../etc/passwd`, absolute paths,
  Windows `..\` separators).
- Symlink entries pointing outside the extraction root.
- Compression bombs — ratios > 100x or single-entry > 100 MiB.
- Required-file forgery — soul.md must exist for the bundle to be a
  valid Hermes profile.

The extractor refuses any of the above and raises a `BundleStructureError`.
"""

from __future__ import annotations

import os
import zipfile
from dataclasses import dataclass
from pathlib import Path

# Hard ceilings — exceeded == reject.
_MAX_BUNDLE_BYTES = 10 * 1024 * 1024  # 10 MiB plaintext zip
_MAX_TOTAL_UNCOMPRESSED = 100 * 1024 * 1024  # 100 MiB
_MAX_ENTRY_BYTES = 50 * 1024 * 1024  # 50 MiB per file
_MAX_ENTRIES = 5000

# Required Hermes profile structure — at minimum.
_REQUIRED_FILES = ("soul.md",)


class BundleStructureError(Exception):
    """Bundle malformed, missing required files, or hostile contents."""


class BundleTooLargeError(BundleStructureError):
    """Bundle exceeds the 10 MiB plaintext upload cap."""


@dataclass(frozen=True)
class ExtractedBundle:
    root: Path
    soul_md_preview: str  # first 500 chars of soul.md


def _is_traversal(name: str) -> bool:
    """Reject any path that escapes the extraction root or is absolute."""
    if not name or name.startswith(("/", "\\")):
        return True
    if ".." in name.replace("\\", "/").split("/"):
        return True
    if ":" in name:  # Windows drive letters
        return True
    return False


def _normalize_zip_member(name: str) -> str:
    """Force forward slashes; lets us reason about a single separator."""
    return name.replace("\\", "/")


def validate_hermes_bundle(raw_zip: bytes) -> None:
    """Cheap pre-extract validation: size, magic, structure, required files.

    Raises `BundleStructureError` (or its `BundleTooLargeError` subclass)
    on any failure. Returns None on success — caller can then call
    `safe_extract_zip` if it needs the bytes on disk.
    """
    if len(raw_zip) > _MAX_BUNDLE_BYTES:
        raise BundleTooLargeError(
            f"bundle exceeds {_MAX_BUNDLE_BYTES // (1024 * 1024)} MiB limit"
        )
    if len(raw_zip) < 22:  # smallest possible zip = empty central dir
        raise BundleStructureError("bundle too small to be a zip")

    # zipfile auto-detects from bytes; we wrap in an in-memory file.
    import io

    bio = io.BytesIO(raw_zip)
    try:
        zf = zipfile.ZipFile(bio)
    except zipfile.BadZipFile as e:
        raise BundleStructureError(f"not a valid zip: {e}") from e

    try:
        infolist = zf.infolist()
    except Exception as e:
        raise BundleStructureError(f"zip metadata read failed: {e}") from e

    if len(infolist) > _MAX_ENTRIES:
        raise BundleStructureError(
            f"too many entries: {len(infolist)} > {_MAX_ENTRIES}"
        )

    total_uncompressed = 0
    member_names: set[str] = set()
    for info in infolist:
        norm = _normalize_zip_member(info.filename)
        if _is_traversal(norm):
            raise BundleStructureError(f"path traversal in zip: {info.filename!r}")
        # Reject symlinks (posix file mode 0xA000).
        if (info.external_attr >> 16) & 0o170000 == 0o120000:
            raise BundleStructureError(f"symlink not allowed: {info.filename!r}")
        if info.file_size < 0:
            raise BundleStructureError("negative file size in zip header")
        if info.file_size > _MAX_ENTRY_BYTES:
            raise BundleStructureError(
                f"entry {info.filename!r} too large: {info.file_size}"
            )
        total_uncompressed += info.file_size
        if total_uncompressed > _MAX_TOTAL_UNCOMPRESSED:
            raise BundleStructureError("total uncompressed size exceeds 100 MiB")
        # Compression bomb heuristic.
        if (
            info.compress_size > 0
            and info.file_size / max(1, info.compress_size) > 200
            and info.file_size > 1 * 1024 * 1024
        ):
            raise BundleStructureError(
                f"suspicious compression ratio for {info.filename!r}"
            )
        member_names.add(norm)

    # Required files check — accept either a flat `soul.md` or a single-dir
    # nested layout (e.g. `my-agent/soul.md`).
    for required in _REQUIRED_FILES:
        present = required in member_names or any(
            n.endswith("/" + required) for n in member_names
        )
        if not present:
            raise BundleStructureError(f"bundle missing required file: {required}")


def safe_extract_zip(raw_zip: bytes, dest_root: Path) -> ExtractedBundle:
    """Validate then extract to `dest_root`. Caller is responsible for cleanup.

    Returns the extraction root and a 500-char preview of soul.md (used
    by the post-decryption similarity layer when added in v2).
    """
    validate_hermes_bundle(raw_zip)

    dest_root.mkdir(parents=True, exist_ok=True)
    dest_root_real = dest_root.resolve()

    import io

    bio = io.BytesIO(raw_zip)
    with zipfile.ZipFile(bio) as zf:
        for info in zf.infolist():
            norm = _normalize_zip_member(info.filename)
            target = (dest_root_real / norm).resolve()
            # Defense in depth: extracted target MUST stay under root.
            try:
                target.relative_to(dest_root_real)
            except ValueError as e:
                raise BundleStructureError(
                    f"resolved path escaped root: {info.filename!r}"
                ) from e

            if norm.endswith("/"):
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as source:
                # Read in capped chunks; never trust the header `file_size`
                # alone because that's what compression bombs lie about.
                written = 0
                with target.open("wb") as out:
                    while True:
                        chunk = source.read(64 * 1024)
                        if not chunk:
                            break
                        written += len(chunk)
                        if written > _MAX_ENTRY_BYTES:
                            raise BundleStructureError(
                                f"entry {info.filename!r} exceeded cap during extract"
                            )
                        out.write(chunk)

    # Locate soul.md and read first 500 chars for preview.
    soul = _find_first(dest_root_real, "soul.md")
    if soul is None:
        raise BundleStructureError("soul.md vanished after extraction")
    try:
        preview = soul.read_text(encoding="utf-8", errors="replace")[:500]
    except OSError as e:
        raise BundleStructureError(f"soul.md unreadable: {e}") from e

    return ExtractedBundle(root=dest_root_real, soul_md_preview=preview)


def _find_first(root: Path, name: str) -> Path | None:
    for dirpath, _, filenames in os.walk(root):
        if name in filenames:
            return Path(dirpath) / name
    return None
