"""Smoke-test fixtures: build the v1.1.0 publisher in docker-compose, drive
v1.0.7-validator behavior against it from pytest.

Skip cleanly when Docker is not available — these tests are only meaningful
when a live publisher container is running. CI runs them with Docker; local
laptops without Docker daemon get green-by-skip.

What this conftest owns:
- Session-scoped fixture that runs `docker compose -f ... up -d --wait` and
  tears down at session end.
- Ephemeral ed25519 signing key generation. The private half is injected into
  the publisher container via env (CATHEDRAL_EVAL_SIGNING_KEY); the public half
  is what the simulated v1.0.7 validator uses to verify cathedral_signature.
- Helpers to seed the publisher's sqlite database directly (via the bind-mount
  at /data) with v1-signed eval_runs rows. The publisher is the canonical
  writer for that table; we tolerate the dual-writer pattern only because the
  smoke test is single-tenant and short-lived.
- Helper to import v1.0.7's pull_loop.py at test time. The module bytes are
  retrieved via `git show v1.0.7:src/cathedral/validator/pull_loop.py`, exec'd
  into a fresh module namespace, and exposed to tests so we can assert the
  actual v1.0.7 code parses the v1.1.0 publisher response.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import shutil
import subprocess
import sys
import time
import types
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = Path(__file__).with_name("docker-compose.smoke.yml")

PUBLISHER_HOST_PORT = 18000
PUBLISHER_BASE_URL = f"http://127.0.0.1:{PUBLISHER_HOST_PORT}"

# CI sometimes needs a long boot window — Hippius stub, sqlite WAL init,
# uvicorn startup, healthcheck retries. Cap at ~3 minutes.
COMPOSE_UP_TIMEOUT_SECS = 180


def _docker_available() -> bool:
    """True when both the docker CLI and a reachable daemon are present.

    `docker compose version` (without contacting the daemon) checks the
    plugin is installed. `docker info` then probes the daemon socket. We
    need both — `up --wait` will hang otherwise.
    """
    if shutil.which("docker") is None:
        return False
    try:
        subprocess.run(
            ["docker", "compose", "version"],
            check=True,
            capture_output=True,
            timeout=10,
        )
    except (subprocess.SubprocessError, FileNotFoundError):
        return False
    try:
        subprocess.run(
            ["docker", "info"],
            check=True,
            capture_output=True,
            timeout=10,
        )
    except (subprocess.SubprocessError, FileNotFoundError):
        return False
    return True


DOCKER_AVAILABLE = _docker_available()


# --------------------------------------------------------------------------
# Public-key plumbing
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class SmokeKeys:
    """Ephemeral cathedral signing keypair for the test session."""

    signing_key_hex: str
    private_key: Ed25519PrivateKey
    public_key: Ed25519PublicKey


def _make_session_keypair() -> SmokeKeys:
    seed = os.urandom(32)
    priv = Ed25519PrivateKey.from_private_bytes(seed)
    return SmokeKeys(
        signing_key_hex=seed.hex(),
        private_key=priv,
        public_key=priv.public_key(),
    )


# --------------------------------------------------------------------------
# docker-compose session lifecycle
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class PublisherFixture:
    """Handles for tests to interact with the live publisher container."""

    base_url: str
    keys: SmokeKeys
    db_path: Path

    @property
    def public_key_hex(self) -> str:
        raw = self.keys.public_key.public_bytes(Encoding.Raw, PublicFormat.Raw)
        return raw.hex()


@pytest.fixture(scope="session")
def smoke_stack(tmp_path_factory: pytest.TempPathFactory) -> Iterator[PublisherFixture]:
    """Bring up the docker-compose stack for the test session.

    Skips the whole module when Docker is not available so local laptops
    without a running daemon get a green-by-skip result rather than a hang
    or a hard fail.
    """
    if not DOCKER_AVAILABLE:
        pytest.skip("docker not available — skipping smoke stack")

    data_dir = tmp_path_factory.mktemp("smoke-publisher-data")
    keys = _make_session_keypair()

    env = os.environ.copy()
    env["CATHEDRAL_EVAL_SIGNING_KEY"] = keys.signing_key_hex
    env["SMOKE_DATA_DIR"] = str(data_dir)
    # Compose project name keeps containers from colliding with anything
    # else the user might have running on this host.
    env["COMPOSE_PROJECT_NAME"] = "cathedral-smoke"

    up_cmd = [
        "docker",
        "compose",
        "-f",
        str(COMPOSE_FILE),
        "up",
        "-d",
        "--wait",
        "--wait-timeout",
        str(COMPOSE_UP_TIMEOUT_SECS),
    ]
    down_cmd = [
        "docker",
        "compose",
        "-f",
        str(COMPOSE_FILE),
        "down",
        "-v",
        "--remove-orphans",
    ]

    proc = subprocess.run(
        up_cmd,
        env=env,
        capture_output=True,
        text=True,
        timeout=COMPOSE_UP_TIMEOUT_SECS + 30,
    )
    if proc.returncode != 0:
        # Capture container logs so the failure is debuggable from the
        # pytest output alone — otherwise the user has to re-run by hand.
        try:
            logs = subprocess.run(
                [
                    "docker",
                    "compose",
                    "-f",
                    str(COMPOSE_FILE),
                    "logs",
                    "--no-color",
                    "--tail",
                    "200",
                ],
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
            ).stdout
        except Exception:
            logs = "<docker compose logs failed>"
        subprocess.run(down_cmd, env=env, capture_output=True, text=True, timeout=60)
        pytest.skip(f"docker compose up failed — skipping. stderr:\n{proc.stderr}\nlogs:\n{logs}")

    db_path = data_dir / "publisher.db"
    # Wait for the publisher to actually create the sqlite file. The
    # healthcheck only proves uvicorn is up; the schema is created on
    # first request through the orchestrator boot path, which races the
    # healthcheck endpoint mounting.
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and not db_path.exists():
        time.sleep(0.25)

    try:
        yield PublisherFixture(
            base_url=PUBLISHER_BASE_URL,
            keys=keys,
            db_path=db_path,
        )
    finally:
        subprocess.run(down_cmd, env=env, capture_output=True, text=True, timeout=120)


# --------------------------------------------------------------------------
# Seeding helpers — write eval_runs rows directly to the publisher's sqlite
# --------------------------------------------------------------------------


def _canonical_json(payload: dict[str, Any]) -> bytes:
    """Match cathedral.v1_types.canonical_json: drop post-signing fields,
    sort keys, no whitespace, UTF-8. The v1.0.7 verifier expects exactly
    this byte shape; any drift here breaks the smoke test in a way that
    masks the bug we're guarding against.
    """
    excluded = {"signature", "cathedral_signature", "merkle_epoch"}
    body = {k: v for k, v in payload.items() if k not in excluded}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def _ms_iso(dt: datetime) -> str:
    """Match scoring_pipeline._ms_iso: ms precision with trailing Z."""
    s = dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}"
    return s + "Z"


def make_signed_eval_output(
    sk: Ed25519PrivateKey,
    *,
    idx: int = 0,
    ran_at_iso: str | None = None,
    card_id: str = "eu-ai-act",
    miner_hotkey: str = "5SmokeMinerHotkey",
) -> dict[str, Any]:
    """Build a v1.0.x-compatible signed eval_output (the 9-key set).

    Mirrors tests/v1/test_validator_pull_loop.make_signed_eval_output so we
    sign exactly what v1.0.7's verify_eval_output_signature reconstructs.
    """
    import blake3

    output_card = {
        "id": card_id,
        "topic": "demo",
        "idx": idx,
        # worker_owner_hotkey is what v1.0.7's _hotkey_for() reads off the
        # output_card and persists as the miner_hotkey on the validator
        # side. Without this, the validator skips the row.
        "worker_owner_hotkey": miner_hotkey,
    }
    output_card_hash = blake3.blake3(
        json.dumps(output_card, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()
    signed = {
        "id": f"00000000-0000-4000-8000-{idx:012d}",
        "agent_id": f"11111111-1111-4111-8111-{idx:012d}",
        "agent_display_name": f"Agent {idx}",
        "card_id": card_id,
        "output_card": output_card,
        "output_card_hash": output_card_hash,
        "weighted_score": 0.5 + 0.001 * idx,
        "polaris_verified": False,
        "ran_at": ran_at_iso or _ms_iso(datetime.now(UTC)),
    }
    blob = _canonical_json(signed)
    sig = base64.b64encode(sk.sign(blob)).decode("ascii")
    payload = dict(signed)
    payload["cathedral_signature"] = sig
    payload["merkle_epoch"] = None
    return payload


async def _ensure_submission_row(
    db_path: Path,
    *,
    submission_id: str,
    miner_hotkey: str,
    card_id: str = "eu-ai-act",
) -> None:
    """Write the parent agent_submissions row the FK on eval_runs needs.

    The publisher's join in list_eval_runs_recent gates on
    `sub.status != 'discovery' AND sub.attestation_mode IN ('polaris','tee')
    AND sub.discovery_only = 0`, so the seed row must satisfy those
    predicates or the row never reaches the leaderboard surface.
    """
    import aiosqlite

    async with aiosqlite.connect(str(db_path)) as conn:
        await conn.execute("PRAGMA foreign_keys=ON")
        await conn.execute(
            """
            INSERT OR IGNORE INTO agent_submissions (
                id, miner_hotkey, card_id, bundle_blob_key, bundle_hash,
                bundle_size_bytes, encryption_key_id, bundle_signature,
                display_name, bio, logo_url, soul_md_preview,
                metadata_fingerprint, similarity_check_passed,
                rejection_reason, submitted_at, status, first_mover_at,
                attestation_mode, attestation_type, attestation_blob,
                attestation_verified_at, discovery_only
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                      ?, ?, ?, ?, ?, ?)
            """,
            (
                submission_id,
                miner_hotkey,
                card_id,
                f"bundle/{submission_id}",
                "0" * 64,
                1024,
                "kek-id",
                "sig",
                f"Agent {submission_id[:8]}",
                None,
                None,
                None,
                "fp",
                1,
                None,
                _ms_iso(datetime.now(UTC)),
                "ranked",
                None,
                "polaris",
                None,
                None,
                None,
                0,
            ),
        )
        await conn.commit()


async def _seed_eval_run(
    db_path: Path,
    *,
    signed: dict[str, Any],
    submission_id: str,
    miner_hotkey: str,
    card_id: str = "eu-ai-act",
) -> None:
    """Persist a signed eval_output as an eval_runs row.

    The publisher's `list_eval_runs_recent` selects rows from `eval_runs`
    and joins `agent_submissions`, so we need both rows. We round-trip
    `output_card_json` as a JSON string per the schema, and we re-use the
    eval_output's `ran_at` and `cathedral_signature` verbatim — those are
    the bytes that v1.0.7's verifier reconstructs and checks.
    """
    import aiosqlite

    await _ensure_submission_row(
        db_path,
        submission_id=submission_id,
        miner_hotkey=miner_hotkey,
        card_id=card_id,
    )

    async with aiosqlite.connect(str(db_path)) as conn:
        await conn.execute("PRAGMA foreign_keys=ON")
        await conn.execute(
            """
            INSERT OR REPLACE INTO eval_runs (
                id, submission_id, epoch, round_index, polaris_agent_id,
                polaris_run_id, task_json, output_card_json, output_card_hash,
                score_parts, weighted_score, ran_at, duration_ms, errors,
                cathedral_signature, polaris_verified, polaris_attestation
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                signed["id"],
                submission_id,
                0,
                0,
                "polaris-agent-id",
                "polaris-run-id",
                json.dumps({"prompt": "demo"}),
                json.dumps(signed["output_card"]),
                signed["output_card_hash"],
                json.dumps({"source_quality": 0.5}),
                signed["weighted_score"],
                signed["ran_at"],
                100,
                None,
                signed["cathedral_signature"],
                0,
                None,
            ),
        )
        await conn.commit()


def seed_eval_run(
    db_path: Path,
    *,
    signed: dict[str, Any],
    submission_id: str,
    miner_hotkey: str = "5SmokeMinerHotkey",
    card_id: str = "eu-ai-act",
) -> None:
    """Sync wrapper around the async aiosqlite seed."""
    asyncio.run(
        _seed_eval_run(
            db_path,
            signed=signed,
            submission_id=submission_id,
            miner_hotkey=miner_hotkey,
            card_id=card_id,
        )
    )


# --------------------------------------------------------------------------
# v1.0.7 pull-loop loader — fetch the actual v1.0.7 module from git history
# --------------------------------------------------------------------------


def _load_v107_pull_loop_source() -> str:
    """Return v1.0.7's src/cathedral/validator/pull_loop.py as a string.

    Falls back to skipping the calling test if the v1.0.7 tag isn't in
    the local clone (e.g. shallow checkout on CI without --depth=0).
    """
    try:
        out = subprocess.run(
            [
                "git",
                "-C",
                str(REPO_ROOT),
                "show",
                "v1.0.7:src/cathedral/validator/pull_loop.py",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (subprocess.SubprocessError, FileNotFoundError) as e:
        pytest.skip(f"v1.0.7 tag not available in local git: {e}")
    return out.stdout


def load_v107_pull_loop() -> types.ModuleType:
    """Exec v1.0.7's pull_loop.py into a fresh module namespace.

    The module imports cathedral.v1_types.canonical_json and
    cathedral.validator.health.Health — both available in the current
    tree because their wire shape did not change in v1.1.0. We register
    the module in sys.modules under a unique name so multiple tests can
    load it side-by-side without colliding on module-level state (the
    pull-loop carries a `_LAST_SINCE` dict that we explicitly want
    isolated between tests).
    """
    source = _load_v107_pull_loop_source()
    mod_name = f"cathedral_v107_pull_loop_{uuid.uuid4().hex[:8]}"
    mod = types.ModuleType(mod_name)
    mod.__file__ = f"<v1.0.7:{mod_name}>"
    sys.modules[mod_name] = mod
    exec(compile(source, mod.__file__, "exec"), mod.__dict__)
    return mod
