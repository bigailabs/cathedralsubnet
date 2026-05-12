"""Unit tests for SshProbeRunner.

The tests mock asyncssh at the module level so we don't need a real SSH
server. Each test verifies one failure mode + the happy path, mirroring
the spec's enumerated `SSH_PROBE_FAILURE_CODES`.
"""

from __future__ import annotations

import importlib.util as _ilu
import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Direct module load to avoid the cathedral.eval.__init__ -> publisher
# import cycle. Re-use an already-registered cathedral.eval.polaris_runner
# if a sibling test has loaded one (so PolarisRunnerError class identity
# matches across all tests). Otherwise load polaris_runner ourselves and
# register it.
_ROOT = Path(__file__).resolve().parents[2]

if "cathedral.eval.polaris_runner" in sys.modules:
    _pr = sys.modules["cathedral.eval.polaris_runner"]
else:
    _PR_PATH = _ROOT / "src" / "cathedral" / "eval" / "polaris_runner.py"
    _pr_spec = _ilu.spec_from_file_location(
        "cathedral.eval.polaris_runner", _PR_PATH
    )
    assert _pr_spec and _pr_spec.loader
    _pr = _ilu.module_from_spec(_pr_spec)
    sys.modules["cathedral.eval.polaris_runner"] = _pr
    _pr_spec.loader.exec_module(_pr)

_SPR_PATH = _ROOT / "src" / "cathedral" / "eval" / "ssh_probe_runner.py"
_spec = _ilu.spec_from_file_location("_ssh_probe_runner_for_test", _SPR_PATH)
assert _spec and _spec.loader
_module = _ilu.module_from_spec(_spec)
sys.modules["_ssh_probe_runner_for_test"] = _module
_spec.loader.exec_module(_module)

SshProbeRunner = _module.SshProbeRunner
SshProbeRunnerConfig = _module.SshProbeRunnerConfig
SshProbeError = _module.SshProbeError
SSH_PROBE_FAILURE_CODES = _module.SSH_PROBE_FAILURE_CODES
package_visit = _module.package_visit
VisitTrace = _module.VisitTrace


# Reuse the real EvalTask
from cathedral.v1_types import EvalTask

# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


@pytest.fixture
def ssh_key_path(tmp_path: Path) -> str:
    """A fake SSH key file the runner can stat without crashing.

    asyncssh is mocked at the call boundary so the key bytes never get
    read by a real ssh client.
    """
    k = tmp_path / "fake_id_ed25519"
    k.write_text("-----BEGIN OPENSSH PRIVATE KEY-----\nfake-bytes\n-----END OPENSSH PRIVATE KEY-----\n")
    return str(k)


@pytest.fixture
def runner_config(ssh_key_path: str) -> SshProbeRunnerConfig:
    return SshProbeRunnerConfig(
        ssh_private_key_path=ssh_key_path,
        connect_timeout_secs=5.0,
        prompt_timeout_secs=30.0,
        visit_budget_secs=120.0,
        connect_retries=2,
        connect_retry_initial_secs=0.01,  # speed tests up
    )


@pytest.fixture
def eval_task() -> EvalTask:
    return EvalTask(
        card_id="eu-ai-act",
        epoch=1,
        round_index=0,
        prompt="Produce the current EU AI Act regulatory card.",
    )


@pytest.fixture
def submission() -> dict[str, Any]:
    return {
        "id": "sub_test_001",
        "ssh_host": "miner.example.com",
        "ssh_port": 22,
        "ssh_user": "cathedral-prober",
        "hermes_port": 8080,
        "encryption_key_id": "kms-local:wrapped:nonce",
    }


@pytest.fixture
def good_card_json() -> dict[str, Any]:
    return {
        "card_id": "eu-ai-act",
        "summary": "EU AI Act enforcement update for Q1 2026.",
        "no_legal_advice": True,
        "citations": [{"url": "https://example.com/source", "hash_blake3": "abc"}],
        "what_changed": ["..."],
        "why_it_matters": "...",
        "action_notes": "...",
        "risks": "...",
    }


def _stream(events: list[dict[str, Any]]) -> str:
    """Render NDJSON like Hermes /chat does."""
    return "\n".join(json.dumps(e) for e in events) + "\n"


def _ssh_result(stdout: str = "", stderr: str = "", exit_status: int = 0) -> Any:
    """A minimal stand-in for asyncssh's SSHCompletedProcess."""
    m = MagicMock()
    m.stdout = stdout
    m.stderr = stderr
    m.exit_status = exit_status
    return m


# --------------------------------------------------------------------------
# Config validation
# --------------------------------------------------------------------------


def test_config_invalid_key_path_raises() -> None:
    with pytest.raises(SshProbeError) as exc:
        SshProbeRunner(
            SshProbeRunnerConfig(ssh_private_key_path="/nonexistent/key")
        )
    assert exc.value.code == "config_invalid"


def test_failure_codes_complete() -> None:
    """If you add a new failure mode in the runner, update this assertion."""
    assert set(SSH_PROBE_FAILURE_CODES) == {
        "connect_refused",
        "auth_failed",
        "hermes_not_found",
        "hermes_unhealthy",
        "prompt_timeout",
        "prompt_error",
        "file_missing",
        "package_failed",
        "transfer_failed",
        "disconnect_dirty",
        "config_invalid",
    }


# --------------------------------------------------------------------------
# Submission validation
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_submission_returns_config_invalid(
    runner_config: SshProbeRunnerConfig, eval_task: EvalTask
) -> None:
    runner = SshProbeRunner(runner_config)
    result = await runner.run(
        bundle_bytes=b"",
        bundle_hash="0" * 64,
        task=eval_task,
        miner_hotkey="5Test" + "x" * 43,
        submission=None,
    )
    assert result.errors
    assert result.errors[0].startswith("config_invalid:")
    assert result.output_card_json == {}
    assert result.manifest is None  # never has manifest


@pytest.mark.asyncio
async def test_missing_ssh_fields_returns_config_invalid(
    runner_config: SshProbeRunnerConfig, eval_task: EvalTask
) -> None:
    runner = SshProbeRunner(runner_config)
    sub_no_host = {"id": "s1", "ssh_user": "u", "hermes_port": 8080}  # no ssh_host
    result = await runner.run(
        bundle_bytes=b"",
        bundle_hash="0" * 64,
        task=eval_task,
        miner_hotkey="5Test" + "x" * 43,
        submission=sub_no_host,
    )
    assert result.errors[0].startswith("config_invalid:")


# --------------------------------------------------------------------------
# Happy path
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_happy_path(
    runner_config: SshProbeRunnerConfig,
    eval_task: EvalTask,
    submission: dict[str, Any],
    good_card_json: dict[str, Any],
) -> None:
    """Healthy SSH, healthy /healthz, /chat returns Card, files collected."""
    runner = SshProbeRunner(runner_config)

    fake_conn = MagicMock()
    fake_conn.run = AsyncMock(
        side_effect=[
            _ssh_result(exit_status=0),                      # /healthz ok
            _ssh_result(exit_status=0),                      # write tmp file
            _ssh_result(                                     # /chat -> Card
                stdout=_stream([
                    {"type": "delta", "content": "{"},
                    {"type": "final", "card_json": good_card_json},
                ]),
                exit_status=0,
            ),
            _ssh_result(stdout="# Soul\n", exit_status=0),   # cat soul.md
            _ssh_result(stdout="# Agents\n", exit_status=0), # cat AGENTS.md
            _ssh_result(stdout="ls output", exit_status=0),  # skills listing
        ]
    )
    fake_conn.close = MagicMock()
    fake_conn.wait_closed = AsyncMock()

    fake_asyncssh = MagicMock()
    fake_asyncssh.connect = AsyncMock(return_value=fake_conn)
    fake_asyncssh.PermissionDenied = type("PermissionDenied", (Exception,), {})
    fake_asyncssh.KeyImportError = type("KeyImportError", (Exception,), {})
    fake_asyncssh.Error = type("AsyncSSHError", (Exception,), {})

    with patch.dict(sys.modules, {"asyncssh": fake_asyncssh}):
        result = await runner.run(
            bundle_bytes=b"",
            bundle_hash="0" * 64,
            task=eval_task,
            miner_hotkey="5HappyPath" + "x" * 38,
            submission=submission,
        )

    assert result.errors == [], f"expected success, got errors: {result.errors}"
    assert result.output_card_json == good_card_json
    assert result.manifest is None, "ssh-probe must not produce a Polaris manifest"
    assert result.attestation is None, "ssh-probe must not produce a Polaris attestation"
    assert result.trace is not None
    assert result.trace["tier"] == "ssh-probe"
    assert result.trace["prompts_succeeded"] == 1
    assert result.trace["hermes_healthz_ok"] is True
    assert "soul.md" in result.trace["files_collected"]
    assert "AGENTS.md" in result.trace["files_collected"]


# --------------------------------------------------------------------------
# Failure modes
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_connect_refused(
    runner_config: SshProbeRunnerConfig,
    eval_task: EvalTask,
    submission: dict[str, Any],
) -> None:
    """OSError on connect, all retries fail -> connect_refused."""
    runner = SshProbeRunner(runner_config)

    fake_asyncssh = MagicMock()
    fake_asyncssh.PermissionDenied = type("PermissionDenied", (Exception,), {})
    fake_asyncssh.KeyImportError = type("KeyImportError", (Exception,), {})
    fake_asyncssh.Error = type("AsyncSSHError", (Exception,), {})
    fake_asyncssh.connect = AsyncMock(
        side_effect=OSError("connection refused")
    )

    with patch.dict(sys.modules, {"asyncssh": fake_asyncssh}):
        result = await runner.run(
            bundle_bytes=b"",
            bundle_hash="0" * 64,
            task=eval_task,
            miner_hotkey="5Refused" + "x" * 40,
            submission=submission,
        )

    assert result.errors[0].startswith("connect_refused:")
    assert fake_asyncssh.connect.await_count == runner_config.connect_retries


@pytest.mark.asyncio
async def test_auth_failed(
    runner_config: SshProbeRunnerConfig,
    eval_task: EvalTask,
    submission: dict[str, Any],
) -> None:
    """PermissionDenied is terminal, no retries."""
    runner = SshProbeRunner(runner_config)

    PermissionDenied = type("PermissionDenied", (Exception,), {})
    fake_asyncssh = MagicMock()
    fake_asyncssh.PermissionDenied = PermissionDenied
    fake_asyncssh.KeyImportError = type("KeyImportError", (Exception,), {})
    fake_asyncssh.Error = type("AsyncSSHError", (Exception,), {})
    fake_asyncssh.connect = AsyncMock(side_effect=PermissionDenied("wrong key"))

    with patch.dict(sys.modules, {"asyncssh": fake_asyncssh}):
        result = await runner.run(
            bundle_bytes=b"",
            bundle_hash="0" * 64,
            task=eval_task,
            miner_hotkey="5AuthFail" + "x" * 39,
            submission=submission,
        )

    assert result.errors[0].startswith("auth_failed:")
    # Auth errors aren't retried
    assert fake_asyncssh.connect.await_count == 1


@pytest.mark.asyncio
async def test_hermes_unhealthy(
    runner_config: SshProbeRunnerConfig,
    eval_task: EvalTask,
    submission: dict[str, Any],
) -> None:
    """healthz returns non-zero -> hermes_unhealthy."""
    runner = SshProbeRunner(runner_config)

    fake_conn = MagicMock()
    fake_conn.run = AsyncMock(
        return_value=_ssh_result(exit_status=7)  # curl failure
    )
    fake_conn.close = MagicMock()
    fake_conn.wait_closed = AsyncMock()

    fake_asyncssh = MagicMock()
    fake_asyncssh.connect = AsyncMock(return_value=fake_conn)
    fake_asyncssh.PermissionDenied = type("PermissionDenied", (Exception,), {})
    fake_asyncssh.KeyImportError = type("KeyImportError", (Exception,), {})
    fake_asyncssh.Error = type("AsyncSSHError", (Exception,), {})

    with patch.dict(sys.modules, {"asyncssh": fake_asyncssh}):
        result = await runner.run(
            bundle_bytes=b"",
            bundle_hash="0" * 64,
            task=eval_task,
            miner_hotkey="5Unhealth" + "x" * 39,
            submission=submission,
        )

    assert result.errors[0].startswith("hermes_unhealthy:")
    assert result.trace["hermes_healthz_ok"] is False


@pytest.mark.asyncio
async def test_prompt_error(
    runner_config: SshProbeRunnerConfig,
    eval_task: EvalTask,
    submission: dict[str, Any],
) -> None:
    """healthz ok, /chat returns nonzero -> prompt_error."""
    runner = SshProbeRunner(runner_config)

    fake_conn = MagicMock()
    fake_conn.run = AsyncMock(
        side_effect=[
            _ssh_result(exit_status=0),                              # /healthz ok
            _ssh_result(exit_status=0),                              # write tmp file
            _ssh_result(stderr="connection refused", exit_status=7), # /chat fails
        ]
    )
    fake_conn.close = MagicMock()
    fake_conn.wait_closed = AsyncMock()

    fake_asyncssh = MagicMock()
    fake_asyncssh.connect = AsyncMock(return_value=fake_conn)
    fake_asyncssh.PermissionDenied = type("PermissionDenied", (Exception,), {})
    fake_asyncssh.KeyImportError = type("KeyImportError", (Exception,), {})
    fake_asyncssh.Error = type("AsyncSSHError", (Exception,), {})

    with patch.dict(sys.modules, {"asyncssh": fake_asyncssh}):
        result = await runner.run(
            bundle_bytes=b"",
            bundle_hash="0" * 64,
            task=eval_task,
            miner_hotkey="5PromptErr" + "x" * 38,
            submission=submission,
        )

    assert result.errors[0].startswith("prompt_error:")


@pytest.mark.asyncio
async def test_prompt_returns_no_parseable_card(
    runner_config: SshProbeRunnerConfig,
    eval_task: EvalTask,
    submission: dict[str, Any],
) -> None:
    """Healthy /chat that returned no final event with card_json."""
    runner = SshProbeRunner(runner_config)

    fake_conn = MagicMock()
    fake_conn.run = AsyncMock(
        side_effect=[
            _ssh_result(exit_status=0),                              # /healthz
            _ssh_result(exit_status=0),                              # write tmp
            _ssh_result(stdout=_stream([{"type": "delta", "content": "hi"}]), exit_status=0),
        ]
    )
    fake_conn.close = MagicMock()
    fake_conn.wait_closed = AsyncMock()

    fake_asyncssh = MagicMock()
    fake_asyncssh.connect = AsyncMock(return_value=fake_conn)
    fake_asyncssh.PermissionDenied = type("PermissionDenied", (Exception,), {})
    fake_asyncssh.KeyImportError = type("KeyImportError", (Exception,), {})
    fake_asyncssh.Error = type("AsyncSSHError", (Exception,), {})

    with patch.dict(sys.modules, {"asyncssh": fake_asyncssh}):
        result = await runner.run(
            bundle_bytes=b"",
            bundle_hash="0" * 64,
            task=eval_task,
            miner_hotkey="5NoParse" + "x" * 40,
            submission=submission,
        )

    assert result.errors[0].startswith("prompt_error:")


@pytest.mark.asyncio
async def test_soul_md_missing(
    runner_config: SshProbeRunnerConfig,
    eval_task: EvalTask,
    submission: dict[str, Any],
    good_card_json: dict[str, Any],
) -> None:
    """Hermes produced a card, but soul.md isn't on the box -> file_missing."""
    runner = SshProbeRunner(runner_config)

    fake_conn = MagicMock()
    fake_conn.run = AsyncMock(
        side_effect=[
            _ssh_result(exit_status=0),                          # /healthz ok
            _ssh_result(exit_status=0),                          # write tmp
            _ssh_result(                                         # /chat ok
                stdout=_stream([{"type": "final", "card_json": good_card_json}]),
                exit_status=0,
            ),
            _ssh_result(stderr="cat: no such file", exit_status=1),  # soul.md missing
        ]
    )
    fake_conn.close = MagicMock()
    fake_conn.wait_closed = AsyncMock()

    fake_asyncssh = MagicMock()
    fake_asyncssh.connect = AsyncMock(return_value=fake_conn)
    fake_asyncssh.PermissionDenied = type("PermissionDenied", (Exception,), {})
    fake_asyncssh.KeyImportError = type("KeyImportError", (Exception,), {})
    fake_asyncssh.Error = type("AsyncSSHError", (Exception,), {})

    with patch.dict(sys.modules, {"asyncssh": fake_asyncssh}):
        result = await runner.run(
            bundle_bytes=b"",
            bundle_hash="0" * 64,
            task=eval_task,
            miner_hotkey="5NoSoul" + "x" * 41,
            submission=submission,
        )

    assert result.errors[0].startswith("file_missing:")


# --------------------------------------------------------------------------
# Packaging helper
# --------------------------------------------------------------------------


def test_package_visit_produces_valid_zip(good_card_json: dict[str, Any]) -> None:
    """Package + re-open the zip and check the manifest + members."""
    import io
    import zipfile

    payload = package_visit(
        miner_hotkey="5Pkg" + "x" * 44,
        run_id="run_test",
        card_json=good_card_json,
        files={
            "soul.md": b"# soul\n",
            "AGENTS.md": b"# agents\n",
        },
        trace={"tier": "ssh-probe", "prompts_succeeded": 1},
    )

    with zipfile.ZipFile(io.BytesIO(payload)) as zf:
        names = set(zf.namelist())
        assert names == {"manifest.json", "card.json", "soul.md", "AGENTS.md"}
        manifest = json.loads(zf.read("manifest.json"))
        assert manifest["miner_hotkey"].startswith("5Pkg")
        assert manifest["trace"]["tier"] == "ssh-probe"
        assert "output_card_hash" in manifest
        card_re = json.loads(zf.read("card.json"))
        assert card_re == good_card_json
