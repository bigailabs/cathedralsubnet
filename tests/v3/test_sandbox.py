"""Sandbox runner tests.

End-to-end behaviour is exercised against SubprocessBackend (the
degraded fallback) so these tests run on any host. DockerBackend is
tested at the argv-construction level so we don't require docker on CI.
"""

from __future__ import annotations

import shutil
from unittest.mock import patch

import pytest

from cathedral.v3.sandbox import (
    DockerBackend,
    SandboxConfig,
    SubprocessBackend,
    available_backend,
    run_in_sandbox,
)

# ---------------------------------------------------------------------------
# SandboxConfig validation
# ---------------------------------------------------------------------------


def test_config_default_is_valid() -> None:
    SandboxConfig().validate()


@pytest.mark.parametrize(
    "image",
    [
        "python:3.11-slim",
        "python",
        "library/python:latest",
        "ghcr.io/foo/bar:abc123",
        "registry.example.com/team/image:v1.2.3",
    ],
)
def test_config_accepts_valid_image_names(image: str) -> None:
    SandboxConfig(image=image).validate()


@pytest.mark.parametrize(
    "image",
    [
        "",
        " python:3",
        "python; rm -rf /",
        "python:3 && curl evil.com",
        "../etc/passwd",
        "Python:3.11",  # uppercase registry part disallowed
        "$(whoami)",
    ],
)
def test_config_rejects_malicious_image_names(image: str) -> None:
    with pytest.raises(ValueError, match="image name"):
        SandboxConfig(image=image).validate()


def test_config_rejects_nonpositive_timeout() -> None:
    with pytest.raises(ValueError):
        SandboxConfig(timeout_seconds=0).validate()
    with pytest.raises(ValueError):
        SandboxConfig(timeout_seconds=-1).validate()


# ---------------------------------------------------------------------------
# SubprocessBackend behaviour
# ---------------------------------------------------------------------------


def test_subprocess_runs_simple_script() -> None:
    b = SubprocessBackend()
    r = b.run("print('hello')", SandboxConfig())
    assert r.ok
    assert r.exit_code == 0
    assert "hello" in r.stdout
    assert r.backend == "subprocess"
    assert r.timed_out is False
    assert r.sandbox_violation is False


def test_subprocess_propagates_nonzero_exit() -> None:
    b = SubprocessBackend()
    r = b.run("raise SystemExit(7)", SandboxConfig())
    assert r.ok is False
    assert r.exit_code == 7


def test_subprocess_enforces_timeout() -> None:
    b = SubprocessBackend()
    r = b.run("import time; time.sleep(10)", SandboxConfig(timeout_seconds=1.0))
    assert r.ok is False
    assert r.timed_out is True
    assert r.error == "timeout"


def test_subprocess_env_is_scrubbed_by_default() -> None:
    # Set a CATHEDRAL_* var; the default allowlist does NOT include it.
    import os

    os.environ["CATHEDRAL_V3_SECRET_TEST_LEAK"] = "should_not_leak"
    try:
        b = SubprocessBackend()
        script = "import os; print(os.environ.get('CATHEDRAL_V3_SECRET_TEST_LEAK', '<absent>'))"
        r = b.run(script, SandboxConfig())
        assert r.ok
        assert "<absent>" in r.stdout, f"secret leaked into sandbox env: {r.stdout!r}"
    finally:
        os.environ.pop("CATHEDRAL_V3_SECRET_TEST_LEAK", None)


def test_subprocess_extra_env_is_passed_through() -> None:
    b = SubprocessBackend()
    r = b.run(
        "import os; print(os.environ.get('CATHEDRAL_SCRATCH', 'missing'))",
        SandboxConfig(extra_env={"CATHEDRAL_SCRATCH": "ok"}),
    )
    assert r.ok
    assert "ok" in r.stdout


def test_subprocess_writes_files_into_workdir() -> None:
    b = SubprocessBackend()
    r = b.run(
        "print(open('input.txt').read())",
        SandboxConfig(),
        files={"input.txt": "hello-from-file"},
    )
    assert r.ok
    assert "hello-from-file" in r.stdout


@pytest.mark.parametrize(
    "bad",
    ["../escape.txt", "/etc/passwd", "", ".", "sub/dir.txt"],
)
def test_subprocess_rejects_path_traversal_in_files(bad: str) -> None:
    b = SubprocessBackend()
    r = b.run("print(1)", SandboxConfig(), files={bad: "x"})
    assert r.ok is False
    assert r.sandbox_violation is True


def test_subprocess_returns_clean_result_on_success_run_in_sandbox() -> None:
    r = run_in_sandbox(
        "print(2+2)",
        SandboxConfig(),
        backend=SubprocessBackend(),
    )
    assert r.ok
    assert "4" in r.stdout


# ---------------------------------------------------------------------------
# DockerBackend argv construction (no actual docker exec)
# ---------------------------------------------------------------------------


def test_docker_argv_includes_isolation_flags() -> None:
    # Force DockerBackend to think docker exists; intercept subprocess.run.
    fake_docker = "/usr/local/bin/docker"
    with patch("shutil.which", return_value=fake_docker):
        b = DockerBackend()
    captured: dict[str, list[str]] = {}

    class _FakeCompleted:
        returncode = 0
        stdout = ""
        stderr = ""

    def _fake_run(argv: list[str], **kw: object) -> _FakeCompleted:
        captured["argv"] = argv
        return _FakeCompleted()

    with patch("cathedral.v3.sandbox.runner.subprocess.run", _fake_run):
        b.run("print(1)", SandboxConfig())

    argv = captured["argv"]
    assert argv[0] == fake_docker
    assert "run" in argv
    assert "--rm" in argv
    assert "--network=none" in argv
    assert "--read-only" in argv
    assert "--cap-drop" in argv and "ALL" in argv
    assert "--security-opt" in argv and "no-new-privileges" in argv
    assert "--user" in argv and "nobody" in argv
    assert "--cpus" in argv
    assert "--memory" in argv


def test_docker_argv_omits_disallowed_env() -> None:
    import os

    os.environ["CATHEDRAL_LEAKY_KEY"] = "x"
    try:
        fake_docker = "/usr/local/bin/docker"
        with patch("shutil.which", return_value=fake_docker):
            b = DockerBackend()
        captured: dict[str, list[str]] = {}

        class _FakeCompleted:
            returncode = 0
            stdout = ""
            stderr = ""

        def _fake_run(argv: list[str], **kw: object) -> _FakeCompleted:
            captured["argv"] = argv
            return _FakeCompleted()

        with patch("cathedral.v3.sandbox.runner.subprocess.run", _fake_run):
            b.run("print(1)", SandboxConfig())

        joined = " ".join(captured["argv"])
        assert "CATHEDRAL_LEAKY_KEY" not in joined
    finally:
        os.environ.pop("CATHEDRAL_LEAKY_KEY", None)


def test_docker_backend_raises_when_docker_missing() -> None:
    with patch("shutil.which", return_value=None), pytest.raises(RuntimeError, match="docker"):
        DockerBackend()


# ---------------------------------------------------------------------------
# available_backend selection
# ---------------------------------------------------------------------------


def test_available_backend_falls_back_to_subprocess_when_no_docker() -> None:
    with patch("cathedral.v3.sandbox.runner.shutil.which", return_value=None):
        b = available_backend()
    assert b.backend_name == "subprocess"


def test_available_backend_respects_prefer_docker_false() -> None:
    b = available_backend(prefer_docker=False)
    assert b.backend_name == "subprocess"


@pytest.mark.skipif(shutil.which("docker") is None, reason="docker not on PATH")
def test_available_backend_prefers_docker_when_daemon_responds() -> None:
    # We only assert the preference when the docker daemon is reachable.
    # `available_backend` probes it; if the daemon is down on this host,
    # it correctly falls back to subprocess.
    import subprocess

    probe = subprocess.run(
        [shutil.which("docker") or "docker", "version", "--format", "ok"],
        capture_output=True,
        text=True,
        timeout=2,
        check=False,
    )
    b = available_backend()
    if probe.returncode == 0:
        assert b.backend_name == "docker"
    else:
        assert b.backend_name == "subprocess"
