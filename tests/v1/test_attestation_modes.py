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


def test_submit_bundle_mode_explicit_returns_pending_check(
    publisher_client, alice_keypair, tmp_path
):
    """Explicit ``attestation_mode=bundle`` returns 202 + status='queued'.

    History of the default:
    - v1: defaulted to ``polaris`` (legacy LLM-shim path, since deprecated)
    - PR #53: flipped default to ``bundle`` (BYO-compute, v2)
    - PR #73: flipped default to ``ssh-probe`` (Tier B canonical path
      for v1.1.0, per cathedralai/cathedral#70 — both Polaris-flavored
      modes gated behind CATHEDRAL_ENABLE_POLARIS_DEPLOY)

    A bare submission with no ``attestation_mode`` now lands as
    ``ssh-probe`` and 400s without ssh_host/ssh_user. Tests that want
    the bundled-card BYO path must pass ``attestation_mode='bundle'``
    explicitly.
    """
    bundle = make_valid_bundle(soul_md="# bundle mode\n")
    resp = _submit_with_mode(
        publisher_client,
        keypair=alice_keypair,
        card_id="eu-ai-act",
        bundle=bundle,
        attestation_mode="bundle",
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


def test_submit_explicit_polaris_mode_rejected_in_v1(publisher_client, alice_keypair):
    """Explicit ``attestation_mode=polaris`` returns 400 in v1.

    Per PR #73 / cathedralai/cathedral#70, both Polaris-flavored modes
    (``polaris`` legacy LLM-shim and ``polaris-deploy`` v2 Polaris-native
    deploy) are gated behind ``CATHEDRAL_ENABLE_POLARIS_DEPLOY``. With
    the flag unset in production, the submit handler returns 400 with
    ``rejection_reason='tier_a_disabled_for_v1'`` and points at the
    Tier B ssh-probe path.
    """
    bundle = make_valid_bundle(soul_md="# explicit polaris\n")
    resp = _submit_with_mode(
        publisher_client,
        keypair=alice_keypair,
        card_id="eu-ai-act",
        bundle=bundle,
        attestation_mode="polaris",
    )
    assert resp.status_code == 400, resp.text
    assert "tier_a_disabled_for_v1" in resp.json()["detail"]


def test_submit_explicit_polaris_deploy_mode_rejected_in_v1(publisher_client, alice_keypair):
    """``attestation_mode=polaris-deploy`` is gated the same way."""
    bundle = make_valid_bundle(soul_md="# explicit polaris-deploy\n")
    resp = _submit_with_mode(
        publisher_client,
        keypair=alice_keypair,
        card_id="eu-ai-act",
        bundle=bundle,
        attestation_mode="polaris-deploy",
    )
    assert resp.status_code == 400, resp.text
    assert "tier_a_disabled_for_v1" in resp.json()["detail"]


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


# --------------------------------------------------------------------------
# ssh-probe — hermes_port deprecated in v1.1.0 (issue #75)
# --------------------------------------------------------------------------


def _submit_ssh_probe(
    client: Any,
    *,
    keypair: Any,
    card_id: str,
    bundle: bytes,
    ssh_host: str | None = "miner.example.com",
    ssh_user: str | None = "cathedral-probe",
    ssh_port: int | None = None,
    hermes_port: int | None = None,
) -> Any:
    """Submit with attestation_mode=ssh-probe and the supplied coords.

    hermes_port is sent for back-compat tests; v1.1.0 logs + ignores it.
    """
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
        "display_name": "SSH Probe Agent",
        "submitted_at": submitted_at,
        "attestation_mode": "ssh-probe",
    }
    if ssh_host is not None:
        data["ssh_host"] = ssh_host
    if ssh_user is not None:
        data["ssh_user"] = ssh_user
    if ssh_port is not None:
        data["ssh_port"] = str(ssh_port)
    if hermes_port is not None:
        data["hermes_port"] = str(hermes_port)
    return client.post("/v1/agents/submit", headers=headers, data=data, files=files)


def test_submit_ssh_probe_without_hermes_port_succeeds(publisher_client, alice_keypair, tmp_path):
    """In v1.1.0, ssh-probe requires only ssh_host + ssh_user.

    The legacy hermes_port field is deprecated (issue #75 — Hermes is
    CLI-shaped, not HTTP-shaped; Cathedral invokes ``hermes chat -q``
    over SSH rather than curling an HTTP endpoint). A submission with
    no hermes_port now returns 202; the row persists hermes_port=NULL.
    """
    bundle = make_valid_bundle(soul_md="# ssh-probe no port\n")
    resp = _submit_ssh_probe(
        publisher_client,
        keypair=alice_keypair,
        card_id="eu-ai-act",
        bundle=bundle,
        ssh_host="miner.example.com",
        ssh_user="cathedral-probe",
    )
    assert resp.status_code == 202, resp.text

    row = _read_submission_row(tmp_path / "publisher.db", resp.json()["id"])
    assert row["attestation_mode"] == "ssh-probe"
    assert row["ssh_host"] == "miner.example.com"
    assert row["ssh_user"] == "cathedral-probe"
    assert row["ssh_port"] == 22  # default
    assert row["hermes_port"] is None


def test_submit_ssh_probe_with_legacy_hermes_port_succeeds_but_ignored(
    publisher_client, alice_keypair, tmp_path
):
    """Back-compat: v1.0.x clients sending hermes_port get 202; value
    is logged and dropped before persistence. The row's hermes_port
    column is NULL even though the wire carried a value.
    """
    bundle = make_valid_bundle(soul_md="# ssh-probe legacy port\n")
    resp = _submit_ssh_probe(
        publisher_client,
        keypair=alice_keypair,
        card_id="eu-ai-act",
        bundle=bundle,
        ssh_host="miner.example.com",
        ssh_user="cathedral-probe",
        hermes_port=18789,  # old client sends this; publisher ignores it
    )
    assert resp.status_code == 202, resp.text

    row = _read_submission_row(tmp_path / "publisher.db", resp.json()["id"])
    assert row["attestation_mode"] == "ssh-probe"
    assert row["hermes_port"] is None, (
        "hermes_port should be NULL even when client sent a value (issue #75)"
    )


def test_submit_ssh_probe_missing_ssh_host_returns_400(publisher_client, alice_keypair):
    """ssh-probe still requires ssh_host."""
    bundle = make_valid_bundle(soul_md="# ssh-probe missing host\n")
    resp = _submit_ssh_probe(
        publisher_client,
        keypair=alice_keypair,
        card_id="eu-ai-act",
        bundle=bundle,
        ssh_host=None,
        ssh_user="cathedral-probe",
    )
    assert resp.status_code == 400, resp.text
    assert "ssh_host" in resp.json()["detail"]


def test_submit_ssh_probe_missing_ssh_user_returns_400(publisher_client, alice_keypair):
    """ssh-probe still requires ssh_user."""
    bundle = make_valid_bundle(soul_md="# ssh-probe missing user\n")
    resp = _submit_ssh_probe(
        publisher_client,
        keypair=alice_keypair,
        card_id="eu-ai-act",
        bundle=bundle,
        ssh_host="miner.example.com",
        ssh_user=None,
    )
    assert resp.status_code == 400, resp.text
    assert "ssh_user" in resp.json()["detail"]
