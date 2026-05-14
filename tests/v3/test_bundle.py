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
