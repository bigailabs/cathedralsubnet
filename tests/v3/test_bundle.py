"""RepoBundle: build, verify, tamper-evidence, materialize."""

from __future__ import annotations

from pathlib import Path

import pytest
from nacl.signing import SigningKey

from cathedral.v3.bundle import (
    RepoBundle,
    build_bundle,
    materialize_bundle,
    verify_bundle,
)
from cathedral.v3.bundle.builder import build_bundle_from_dir
from cathedral.v3.receipt import ReceiptSigner


@pytest.fixture()
def signer() -> ReceiptSigner:
    return ReceiptSigner(SigningKey.generate())


def _files() -> dict[str, str]:
    return {
        "src/foo.py": "def foo():\n    return 1\n",
        "src/bar.py": "def bar():\n    return 2\n",
        "tests/test_foo.py": "from src.foo import foo\nassert foo() == 1\n",
    }


# ---------------------------------------------------------------------------
# build + verify happy path
# ---------------------------------------------------------------------------


def test_build_bundle_signs_and_verifies(signer: ReceiptSigner) -> None:
    b = build_bundle(_files(), signer=signer, label="foo@v1")
    assert b.bundle_id.startswith("bundle_")
    assert len(b.entries) == 3
    assert b.signer_pubkey_hex == signer.public_hex
    v = verify_bundle(b)
    assert v.ok
    assert v.per_file_ok and v.aggregate_ok and v.signature_ok


def test_build_bundle_is_deterministic_for_same_input(signer: ReceiptSigner) -> None:
    a = build_bundle(_files(), signer=signer)
    b = build_bundle(_files(), signer=signer)
    # bundle_id derives from contents, so it should be stable
    assert a.bundle_id == b.bundle_id
    assert a.aggregate_blake3_hex == b.aggregate_blake3_hex


def test_build_bundle_differs_when_content_differs(signer: ReceiptSigner) -> None:
    files = _files()
    a = build_bundle(files, signer=signer)
    files["src/foo.py"] = files["src/foo.py"].replace("return 1", "return 2")
    b = build_bundle(files, signer=signer)
    assert a.bundle_id != b.bundle_id
    assert a.aggregate_blake3_hex != b.aggregate_blake3_hex


def test_build_bundle_rejects_empty(signer: ReceiptSigner) -> None:
    with pytest.raises(ValueError, match="empty bundle"):
        build_bundle({}, signer=signer)


# ---------------------------------------------------------------------------
# path safety
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        "/abs/path.py",
        "../escape.py",
        "src/../../../etc/passwd",
        "src/./still_bad.py",
        "",
        "src/",  # trailing empty seg
        "src\\windows.py",
        "src/seg with space.py",
    ],
)
def test_build_bundle_rejects_unsafe_paths(signer: ReceiptSigner, bad: str) -> None:
    with pytest.raises(ValueError, match="unsafe bundle path"):
        build_bundle({bad: "x"}, signer=signer)


# ---------------------------------------------------------------------------
# tamper evidence
# ---------------------------------------------------------------------------


def test_tampering_with_file_content_fails_verification(signer: ReceiptSigner) -> None:
    b = build_bundle(_files(), signer=signer)
    forged = b.model_copy(
        update={
            "contents": {**b.contents, "src/foo.py": "def foo():\n    return 999\n"},
        }
    )
    v = verify_bundle(forged)
    assert v.ok is False
    assert v.per_file_ok is False
    assert "hash mismatch" in (v.reason or "")


def test_tampering_with_entry_hash_fails_verification(signer: ReceiptSigner) -> None:
    b = build_bundle(_files(), signer=signer)
    # flip one hex char in the first entry's hash
    rebuilt = b.entries[:]
    e0 = rebuilt[0]
    flipped = e0.blake3_hex[:-1] + ("a" if e0.blake3_hex[-1] != "a" else "b")
    rebuilt[0] = e0.model_copy(update={"blake3_hex": flipped})
    forged = b.model_copy(update={"entries": rebuilt})
    v = verify_bundle(forged)
    assert v.ok is False


def test_tampering_with_aggregate_fails(signer: ReceiptSigner) -> None:
    b = build_bundle(_files(), signer=signer)
    forged = b.model_copy(update={"aggregate_blake3_hex": "0" * 64})
    v = verify_bundle(forged)
    assert v.ok is False
    assert v.aggregate_ok is False


def test_tampering_with_signature_fails(signer: ReceiptSigner) -> None:
    b = build_bundle(_files(), signer=signer)
    forged = b.model_copy(update={"signature_hex": "0" * 128})
    v = verify_bundle(forged)
    assert v.ok is False
    assert v.signature_ok is False


def test_verify_with_unexpected_pubkey_fails(signer: ReceiptSigner) -> None:
    b = build_bundle(_files(), signer=signer)
    other = ReceiptSigner(SigningKey.generate())
    v = verify_bundle(b, expected_pubkey_hex=other.public_hex)
    assert v.ok is False
    assert v.signature_ok is False


# ---------------------------------------------------------------------------
# materialize
# ---------------------------------------------------------------------------


def test_materialize_writes_files_and_reverifies(signer: ReceiptSigner, tmp_path: Path) -> None:
    b = build_bundle(_files(), signer=signer)
    dest = tmp_path / "out"
    v = materialize_bundle(b, dest)
    assert v.ok
    for entry in b.entries:
        assert (dest / entry.path).exists()
        assert (dest / entry.path).read_text() == b.contents[entry.path]


def test_materialize_refuses_nonempty_dest(signer: ReceiptSigner, tmp_path: Path) -> None:
    b = build_bundle(_files(), signer=signer)
    dest = tmp_path / "out"
    dest.mkdir()
    (dest / "existing.txt").write_text("x")
    v = materialize_bundle(b, dest)
    assert v.ok is False
    assert "not empty" in (v.reason or "")


# ---------------------------------------------------------------------------
# from_dir
# ---------------------------------------------------------------------------


def test_build_bundle_from_dir(signer: ReceiptSigner, tmp_path: Path) -> None:
    src = tmp_path / "repo"
    src.mkdir()
    (src / "a.py").write_text("print('a')\n")
    (src / "sub").mkdir()
    (src / "sub" / "b.py").write_text("print('b')\n")
    b = build_bundle_from_dir(src, signer=signer, label="repo@head")
    paths = {e.path for e in b.entries}
    assert paths == {"a.py", "sub/b.py"}
    v = verify_bundle(b)
    assert v.ok


def test_round_trip_via_model_dump(signer: ReceiptSigner) -> None:
    # Bundles should survive JSON serialization without breaking verification.
    b = build_bundle(_files(), signer=signer)
    blob = b.model_dump_json()
    reloaded = RepoBundle.model_validate_json(blob)
    v = verify_bundle(reloaded)
    assert v.ok


# ---------------------------------------------------------------------------
# trust-boundary: externally supplied bundles
# ---------------------------------------------------------------------------


def _forge_unsafe_bundle(signer: ReceiptSigner, unsafe_path: str) -> RepoBundle:
    """Build a real bundle, then forge a self-signed copy with an unsafe path.

    This is what an attacker who controls the signing key would do.
    `build_bundle` rejects unsafe paths up front, so we have to build a
    safe bundle first and then mutate it via `model_copy` and re-sign,
    bypassing `_validate_path` at construction. `verify_bundle` is the
    only thing standing between us and writing outside `dest`.
    """
    from cathedral.v3.bundle.builder import RepoBundleEntry

    body = "x = 1\n"
    raw = body.encode("utf-8")
    entry = RepoBundleEntry(
        path=unsafe_path,
        size_bytes=len(raw),
        blake3_hex=__import__("blake3").blake3(raw).hexdigest(),
    )
    forged = RepoBundle(
        bundle_id="bundle_unsafe_test",
        label="forged",
        created_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
        entries=[entry],
        contents={unsafe_path: body},
        aggregate_blake3_hex="",
        signer_pubkey_hex=signer.public_hex,
        signature_hex="",
    )
    forged = forged.model_copy(update={"aggregate_blake3_hex": forged.recompute_aggregate()})
    sig = signer.sign_bytes(forged.canonical_manifest())
    return forged.model_copy(update={"signature_hex": sig})


@pytest.mark.parametrize(
    "unsafe",
    [
        "../escape.txt",
        "../../escape.txt",
        "/etc/passwd",
        "./still_bad.py",
        "",
        "..",
        ".",
        "src\\windows.py",
        "src/seg with space.py",
        "src/../escape.py",
    ],
)
def test_verify_rejects_externally_supplied_unsafe_paths(
    signer: ReceiptSigner, unsafe: str
) -> None:
    forged = _forge_unsafe_bundle(signer, unsafe)
    v = verify_bundle(forged)
    assert v.ok is False, f"verify_bundle accepted unsafe path: {unsafe!r}"
    assert v.per_file_ok is False
    assert "unsafe" in (v.reason or "")


def test_materialize_refuses_to_escape_dest(signer: ReceiptSigner, tmp_path: Path) -> None:
    forged = _forge_unsafe_bundle(signer, "../escape.txt")
    dest = tmp_path / "out"
    v = materialize_bundle(forged, dest)
    assert v.ok is False
    # The escape target should NOT exist.
    assert not (tmp_path / "escape.txt").exists()


def test_materialize_refuses_absolute_path_entry(signer: ReceiptSigner, tmp_path: Path) -> None:
    forged = _forge_unsafe_bundle(signer, "/tmp/cathedral_v3_escape_test_file.txt")
    dest = tmp_path / "out"
    v = materialize_bundle(forged, dest)
    assert v.ok is False
    # The absolute escape path should NOT have been written.
    assert not Path("/tmp/cathedral_v3_escape_test_file.txt").exists()


def test_materialize_writes_legitimate_nested_paths(signer: ReceiptSigner, tmp_path: Path) -> None:
    # Nested safe paths still work end-to-end.
    deep_files = {
        "a.py": "print('a')\n",
        "src/inner/b.py": "print('b')\n",
        "src/inner/deeper/c.py": "print('c')\n",
    }
    b = build_bundle(deep_files, signer=signer, label="nested")
    dest = tmp_path / "out"
    v = materialize_bundle(b, dest)
    assert v.ok
    for rel in deep_files:
        assert (dest / rel).read_text() == deep_files[rel]
