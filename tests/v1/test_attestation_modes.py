"""POST /v1/agents/submit — attestation_mode branching.

Pins the three-tier intake contract:

- ``polaris``     : back-compat (no attestation form fields); status
                    becomes ``pending_check``; row has
                    ``attestation_mode='polaris'``.
- ``tee``         : Nitro attestation verified end-to-end; row has
                    ``attestation_mode='tee'`` and
                    ``attestation_verified_at`` populated.
- ``unverified``  : discovery-only; status becomes ``discovery``; row has
                    ``discovery_only=1`` and never enters the eval queue.

Bad mode and bad Nitro docs return 4xx with the contract-shaped detail.
TDX and SEV-SNP return 501.
"""

from __future__ import annotations

import base64
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from cathedral.attestation import approved_runtimes
from cathedral.attestation.nitro import _NITRO_ROOT_G1_PEM_DEFAULT
from tests.v1.conftest import (
    CONTRACT_HOTKEY_HEADER,
    CONTRACT_SIGNATURE_HEADER,
    _now_iso_ms,
    blake3_hex,
    make_valid_bundle,
    sign_submission_payload,
    submit_multipart,
)
from tests.v1.nitro_fixtures import (
    build_chain,
    build_nitro_attestation_doc,
    make_pcr8_hex,
)

# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _submit_with_mode(
    client: Any,
    *,
    keypair: Any,
    card_id: str,
    bundle: bytes,
    attestation_mode: str,
    attestation: str | None = None,
    attestation_type: str | None = None,
    display_name: str = "Mode Test Agent",
) -> Any:
    submitted_at = _now_iso_ms()
    bundle_hash = blake3_hex(bundle)
    sig_b64 = sign_submission_payload(
        keypair,
        bundle_hash=bundle_hash,
        card_id=card_id,
        submitted_at=submitted_at,
    )
    headers = {
        CONTRACT_SIGNATURE_HEADER: sig_b64,
        CONTRACT_HOTKEY_HEADER: keypair.ss58_address,
    }
    files = {"bundle": ("agent.zip", bundle, "application/zip")}
    data: dict[str, str] = {
        "card_id": card_id,
        "display_name": display_name,
        "submitted_at": submitted_at,
        "attestation_mode": attestation_mode,
    }
    if attestation is not None:
        data["attestation"] = attestation
    if attestation_type is not None:
        data["attestation_type"] = attestation_type
    return client.post("/v1/agents/submit", headers=headers, data=data, files=files)


def _read_submission_row(db_path: Path, submission_id: str) -> dict[str, Any]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute("SELECT * FROM agent_submissions WHERE id = ?", (submission_id,))
        row = cur.fetchone()
    finally:
        conn.close()
    assert row is not None, f"row {submission_id} not found"
    return dict(row)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """Path the publisher_app fixture wrote to. Mirrors v1 conftest layout."""
    # tmp_path is the per-test directory; v1 conftest writes publisher.db
    # in that directory via publisher_app(tmp_path).
    return tmp_path / "publisher.db"


# --------------------------------------------------------------------------
# polaris mode (default, back-compat)
# --------------------------------------------------------------------------


def test_submit_polaris_mode_default_returns_pending_check(
    publisher_client, alice_keypair, tmp_path
):
    """Omitting attestation_mode defaults to ``bundle`` (BYO-compute, v2).

    Was ``polaris`` in v1; PR #53 changed the default to ``bundle`` after
    the legacy Polaris runtime-evaluate shim proved unreliable in
    production. Tests that explicitly want the legacy path now pass
    ``attestation_mode='polaris'``.
    """
    bundle = make_valid_bundle(soul_md="# default mode\n")
    resp = submit_multipart(
        publisher_client,
        keypair=alice_keypair,
        card_id="eu-ai-act",
        bundle=bundle,
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["status"] == "pending_check"

    row = _read_submission_row(tmp_path / "publisher.db", body["id"])
    assert row["attestation_mode"] == "bundle"
    assert row["attestation_type"] is None
    assert row["attestation_blob"] is None
    assert row["attestation_verified_at"] is None
    assert row["discovery_only"] == 0
    assert row["status"] == "queued"


def test_submit_explicit_polaris_mode(publisher_client, alice_keypair, tmp_path):
    """Explicit ``attestation_mode=polaris`` matches the default path."""
    bundle = make_valid_bundle(soul_md="# explicit polaris\n")
    resp = _submit_with_mode(
        publisher_client,
        keypair=alice_keypair,
        card_id="eu-ai-act",
        bundle=bundle,
        attestation_mode="polaris",
    )
    assert resp.status_code == 202, resp.text
    assert resp.json()["status"] == "pending_check"
    row = _read_submission_row(tmp_path / "publisher.db", resp.json()["id"])
    assert row["attestation_mode"] == "polaris"


# --------------------------------------------------------------------------
# Bad mode -> 400
# --------------------------------------------------------------------------


def test_submit_bad_attestation_mode_returns_400(publisher_client, alice_keypair):
    bundle = make_valid_bundle(soul_md="# bad mode\n")
    resp = _submit_with_mode(
        publisher_client,
        keypair=alice_keypair,
        card_id="eu-ai-act",
        bundle=bundle,
        attestation_mode="not-a-real-mode",
    )
    assert resp.status_code == 400, resp.text
    assert "attestation_mode" in resp.json()["detail"]


# --------------------------------------------------------------------------
# unverified -> 'discovery'
# --------------------------------------------------------------------------


def test_submit_unverified_returns_discovery_no_eval(publisher_client, alice_keypair, tmp_path):
    bundle = make_valid_bundle(soul_md="# unverified discovery probe\n")
    resp = _submit_with_mode(
        publisher_client,
        keypair=alice_keypair,
        card_id="eu-ai-act",
        bundle=bundle,
        attestation_mode="unverified",
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["status"] == "discovery"
    assert "bundle_hash" in body
    assert body["submitted_at"].endswith("Z")

    row = _read_submission_row(tmp_path / "publisher.db", body["id"])
    assert row["status"] == "discovery"
    assert row["attestation_mode"] == "unverified"
    assert row["discovery_only"] == 1
    assert row["attestation_blob"] is None
    # First-mover anchor is NOT set for discovery rows — they must not
    # poison the polaris/tee first-mover race for the same fingerprint.
    assert row["first_mover_at"] is None


def test_submit_unverified_skips_similarity_check(
    publisher_client, alice_keypair, bob_keypair, tmp_path
):
    """Discovery submissions don't go through the similarity gate — same
    display_name + bundle from a different miner is fine."""
    bundle_a = make_valid_bundle(soul_md="# A1\n", extra_files={"pad": b"X" * 4096})
    bundle_b = make_valid_bundle(soul_md="# B1\n", extra_files={"pad": b"X" * 4096})

    first = _submit_with_mode(
        publisher_client,
        keypair=alice_keypair,
        card_id="eu-ai-act",
        bundle=bundle_a,
        attestation_mode="unverified",
        display_name="Same Name",
    )
    assert first.status_code == 202, first.text

    second = _submit_with_mode(
        publisher_client,
        keypair=bob_keypair,
        card_id="eu-ai-act",
        bundle=bundle_b,
        attestation_mode="unverified",
        display_name="Same Name",
    )
    assert second.status_code == 202, second.text
    assert second.json()["status"] == "discovery"


# --------------------------------------------------------------------------
# tee mode — Nitro v1 happy path + failure paths
# --------------------------------------------------------------------------


@pytest.fixture
def nitro_setup(monkeypatch: pytest.MonkeyPatch):
    """Replace the bundled AWS root with a test root and approve a PCR8.

    The approved_runtimes module uses a frozen set, so we monkeypatch the
    module-level value rather than mutating it.
    """
    chain = build_chain()
    monkeypatch.setenv("CATHEDRAL_NITRO_ROOT_PEM", chain.root_pem.decode("ascii"))
    pcr8_hex = make_pcr8_hex(fill=0xAB)
    monkeypatch.setattr(approved_runtimes, "APPROVED_NITRO_PCR8", frozenset({pcr8_hex}))
    yield chain, pcr8_hex
    # monkeypatch reverts both at teardown.


def test_submit_tee_nitro_happy_path(publisher_client, alice_keypair, tmp_path, nitro_setup):
    chain, pcr8_hex = nitro_setup
    bundle = make_valid_bundle(soul_md="# tee happy\n")
    bundle_hash = blake3_hex(bundle)

    doc = build_nitro_attestation_doc(
        chain=chain,
        bundle_hash=bundle_hash,
        card_id="eu-ai-act",
        pcr8_hex=pcr8_hex,
    )
    resp = _submit_with_mode(
        publisher_client,
        keypair=alice_keypair,
        card_id="eu-ai-act",
        bundle=bundle,
        attestation_mode="tee",
        attestation=base64.b64encode(doc).decode("ascii"),
        attestation_type="nitro-v1",
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["status"] == "pending_check"

    row = _read_submission_row(tmp_path / "publisher.db", body["id"])
    assert row["attestation_mode"] == "tee"
    assert row["attestation_type"] == "nitro-v1"
    assert row["attestation_blob"] == doc
    assert row["attestation_verified_at"] is not None
    assert row["status"] == "queued"


def test_submit_tee_nitro_bad_signature_returns_401(
    publisher_client, alice_keypair, tmp_path, nitro_setup
):
    chain, pcr8_hex = nitro_setup
    bundle = make_valid_bundle(soul_md="# tee bad sig\n")
    bundle_hash = blake3_hex(bundle)

    doc = build_nitro_attestation_doc(
        chain=chain,
        bundle_hash=bundle_hash,
        card_id="eu-ai-act",
        pcr8_hex=pcr8_hex,
    )
    # Flip a byte in the signature region (last block of bytes) — the
    # parser still works, signature verification fails.
    tampered = bytearray(doc)
    tampered[-5] ^= 0xFF
    resp = _submit_with_mode(
        publisher_client,
        keypair=alice_keypair,
        card_id="eu-ai-act",
        bundle=bundle,
        attestation_mode="tee",
        attestation=base64.b64encode(bytes(tampered)).decode("ascii"),
        attestation_type="nitro-v1",
    )
    assert resp.status_code == 401, resp.text
    detail = resp.json()["detail"]
    assert detail.startswith("tee attestation invalid:"), detail
    # Row must NOT have been written.
    db_file = tmp_path / "publisher.db"
    conn = sqlite3.connect(str(db_file))
    try:
        n = conn.execute(
            "SELECT COUNT(*) FROM agent_submissions WHERE bundle_hash = ?",
            (bundle_hash,),
        ).fetchone()[0]
    finally:
        conn.close()
    assert n == 0


def test_submit_tee_nitro_pcr_not_approved_returns_401(
    publisher_client, alice_keypair, tmp_path, monkeypatch
):
    """A valid Nitro doc whose PCR8 isn't in the approved set is rejected
    with the same 401 envelope as a structurally bad attestation."""
    chain = build_chain()
    monkeypatch.setenv("CATHEDRAL_NITRO_ROOT_PEM", chain.root_pem.decode("ascii"))
    # Approved list intentionally empty.
    monkeypatch.setattr(approved_runtimes, "APPROVED_NITRO_PCR8", frozenset())
    bundle = make_valid_bundle(soul_md="# tee unapproved\n")
    bundle_hash = blake3_hex(bundle)
    doc = build_nitro_attestation_doc(
        chain=chain,
        bundle_hash=bundle_hash,
        card_id="eu-ai-act",
        pcr8_hex=make_pcr8_hex(fill=0x42),
    )
    resp = _submit_with_mode(
        publisher_client,
        keypair=alice_keypair,
        card_id="eu-ai-act",
        bundle=bundle,
        attestation_mode="tee",
        attestation=base64.b64encode(doc).decode("ascii"),
        attestation_type="nitro-v1",
    )
    assert resp.status_code == 401, resp.text
    assert resp.json()["detail"].startswith("tee attestation invalid:")


def test_submit_tee_missing_attestation_returns_400(publisher_client, alice_keypair):
    bundle = make_valid_bundle(soul_md="# tee missing attestation\n")
    resp = _submit_with_mode(
        publisher_client,
        keypair=alice_keypair,
        card_id="eu-ai-act",
        bundle=bundle,
        attestation_mode="tee",
        attestation=None,
        attestation_type="nitro-v1",
    )
    assert resp.status_code == 400, resp.text


def test_submit_tee_missing_attestation_type_returns_400(publisher_client, alice_keypair):
    bundle = make_valid_bundle(soul_md="# tee missing type\n")
    resp = _submit_with_mode(
        publisher_client,
        keypair=alice_keypair,
        card_id="eu-ai-act",
        bundle=bundle,
        attestation_mode="tee",
        attestation=base64.b64encode(b"placeholder").decode("ascii"),
        attestation_type=None,
    )
    assert resp.status_code == 400, resp.text


def test_submit_tee_bad_attestation_type_returns_400(publisher_client, alice_keypair):
    bundle = make_valid_bundle(soul_md="# tee bad type\n")
    resp = _submit_with_mode(
        publisher_client,
        keypair=alice_keypair,
        card_id="eu-ai-act",
        bundle=bundle,
        attestation_mode="tee",
        attestation=base64.b64encode(b"placeholder").decode("ascii"),
        attestation_type="something-else-v9",
    )
    assert resp.status_code == 400, resp.text


def test_submit_tee_user_data_binding_mismatch_returns_401(
    publisher_client, alice_keypair, nitro_setup
):
    """If the attestation user_data binds a different bundle_hash than
    the uploaded bundle, the verifier rejects with 401."""
    chain, pcr8_hex = nitro_setup
    bundle = make_valid_bundle(soul_md="# tee binding mismatch\n")
    # Build the attestation against a DIFFERENT hash.
    doc = build_nitro_attestation_doc(
        chain=chain,
        bundle_hash="00" * 32,
        card_id="eu-ai-act",
        pcr8_hex=pcr8_hex,
    )
    resp = _submit_with_mode(
        publisher_client,
        keypair=alice_keypair,
        card_id="eu-ai-act",
        bundle=bundle,
        attestation_mode="tee",
        attestation=base64.b64encode(doc).decode("ascii"),
        attestation_type="nitro-v1",
    )
    assert resp.status_code == 401, resp.text
    assert (
        "user_data" in resp.json()["detail"].lower()
        or "bundle_hash" in resp.json()["detail"].lower()
    )


# --------------------------------------------------------------------------
# Stubs: TDX / SEV-SNP -> 501
# --------------------------------------------------------------------------


@pytest.mark.parametrize("att_type", ["tdx-v1", "sev-snp-v1"])
def test_submit_tee_tdx_or_sev_snp_returns_501(publisher_client, alice_keypair, att_type):
    bundle = make_valid_bundle(soul_md=f"# tee {att_type}\n")
    resp = _submit_with_mode(
        publisher_client,
        keypair=alice_keypair,
        card_id="eu-ai-act",
        bundle=bundle,
        attestation_mode="tee",
        attestation=base64.b64encode(b"any-bytes").decode("ascii"),
        attestation_type=att_type,
    )
    assert resp.status_code == 501, resp.text
    detail = resp.json()["detail"]
    assert "pending" in detail
    assert "Nitro" in detail


# --------------------------------------------------------------------------
# Bundled trusted root is the published AWS Nitro Enclaves Root-G1.
# --------------------------------------------------------------------------


def test_bundled_nitro_root_is_aws_g1():
    """Sanity check: the bundled root parses and is self-signed."""
    from cryptography import x509

    cert = x509.load_pem_x509_certificate(_NITRO_ROOT_G1_PEM_DEFAULT)
    # Subject == Issuer for a self-signed root.
    assert cert.subject.rfc4514_string() == cert.issuer.rfc4514_string()
