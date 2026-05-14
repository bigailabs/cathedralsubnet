"""Sandbox runner: Docker preferred, subprocess as a degraded fallback.

This module is the choke point for executing miner-supplied or
fixture code as part of coding jobs. The Docker backend enforces real
isolation; the subprocess backend exists ONLY so tests and CI without
Docker can still exercise the loop, and it is explicitly marked as
degraded in the result so scoring can refuse it for training data.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

# Docker image names: registry/path:tag or library:tag. Strict but tolerant.
_IMAGE_NAME_RE = re.compile(
    r"^[a-z0-9]+(?:[._-][a-z0-9]+)*"
    r"(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)*"
    r"(?::[A-Za-z0-9_.-]{1,128})?$"
)

# Env vars we will pass through to the sandbox. Anything not in this
# list (CATHEDRAL_*, AWS_*, OPENAI_*, HOME, USER, ...) is scrubbed.
_DEFAULT_ENV_ALLOWLIST: frozenset[str] = frozenset(
    {
        "PATH",
        "LANG",
        "LC_ALL",
        "PYTHONUNBUFFERED",
        "PYTHONDONTWRITEBYTECODE",
    }
)


@dataclass(frozen=True)
class SandboxConfig:
    """Per-run sandbox configuration."""

    image: str = "python:3.11-slim"
    timeout_seconds: float = 30.0
    cpu_limit: str = "1.0"
    memory_limit: str = "512m"
    network_disabled: bool = True
    env_allowlist: frozenset[str] = field(default_factory=lambda: _DEFAULT_ENV_ALLOWLIST)
    extra_env: dict[str, str] = field(default_factory=dict)

    def validate(self) -> None:
        if not _IMAGE_NAME_RE.match(self.image):
            raise ValueError(f"image name fails strict validation: {self.image!r}")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be > 0")


@dataclass
class SandboxResult:
    """Uniform result shape across backends."""

    ok: bool
    exit_code: int | None
    stdout: str
    stderr: str
    duration_seconds: float
    backend: str  # "docker" | "subprocess"
    timed_out: bool = False
    sandbox_violation: bool = False
    error: str | None = None


class SandboxBackend(Protocol):
    backend_name: str

    def run(
        self,
        script: str,
        config: SandboxConfig,
        files: dict[str, str] | None = None,
    ) -> SandboxResult: ...


# ---------------------------------------------------------------------------
# Docker backend
# ---------------------------------------------------------------------------


class DockerBackend:
    """Run `script` inside a Docker container with strict isolation.

    Enforced flags:
      --rm                  : container disappears after exit
      --network=none        : no DNS, no outbound, no localhost
      --read-only           : root FS is read-only (we provide a small
                              writable tmpfs at /work)
      --tmpfs /work:size=64m: only writable area
      --cpus / --memory     : per-config limits
      --cap-drop=ALL        : no Linux capabilities
      --security-opt=...    : no_new_privileges
      --user nobody         : non-root in the container
      -w /work              : cwd is the writable tmpfs
      -e <allowlisted>      : only listed env vars propagate
    """

    backend_name = "docker"

    def __init__(self, docker_bin: str | None = None) -> None:
        resolved = docker_bin or shutil.which("docker")
        if resolved is None:
            raise RuntimeError(
                "docker binary not found in PATH; install Docker or use SubprocessBackend"
            )
        self.docker_bin: str = resolved

    def run(
        self,
        script: str,
        config: SandboxConfig,
        files: dict[str, str] | None = None,
    ) -> SandboxResult:
        import time

        config.validate()
        files = files or {}
        for name in files:
            if "/" in name or name.startswith(".") or name in {"", "."}:
                return SandboxResult(
                    ok=False,
                    exit_code=None,
                    stdout="",
                    stderr="",
                    duration_seconds=0.0,
                    backend=self.backend_name,
                    sandbox_violation=True,
                    error=f"file name fails validation: {name!r}",
                )

        # Stage files in a host-side tempdir, copied in via docker cp before exec.
        # We cannot bind-mount (no host mounts), so we cat them through stdin.
        # The script is the entrypoint.
        env_args: list[str] = []
        for k in sorted(config.env_allowlist):
            if k in os.environ:
                env_args += ["-e", f"{k}={os.environ[k]}"]
        for k, v in config.extra_env.items():
            env_args += ["-e", f"{k}={v}"]

        argv = [
            self.docker_bin,
            "run",
            "--rm",
            "--network=none" if config.network_disabled else "--network=bridge",
            "--read-only",
            "--tmpfs",
            "/work:rw,size=64m,mode=1777",
            "--cpus",
            config.cpu_limit,
            "--memory",
            config.memory_limit,
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--user",
            "nobody",
            "-w",
            "/work",
            "-i",
            *env_args,
            config.image,
            "python",
            "-c",
            _bootstrap_program(files, script),
        ]

        start = time.monotonic()
        try:
            r = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=config.timeout_seconds,
                shell=False,
                check=False,
            )
        except subprocess.TimeoutExpired as e:
            return SandboxResult(
                ok=False,
                exit_code=None,
                stdout=(e.stdout or b"").decode("utf-8", "replace") if e.stdout else "",
                stderr=(e.stderr or b"").decode("utf-8", "replace") if e.stderr else "",
                duration_seconds=time.monotonic() - start,
                backend=self.backend_name,
                timed_out=True,
                error="timeout",
            )
        except OSError as e:
            return SandboxResult(
                ok=False,
                exit_code=None,
                stdout="",
                stderr="",
                duration_seconds=time.monotonic() - start,
                backend=self.backend_name,
                error=f"docker exec failed: {e}",
            )

        return SandboxResult(
            ok=r.returncode == 0,
            exit_code=r.returncode,
            stdout=r.stdout,
            stderr=r.stderr,
            duration_seconds=time.monotonic() - start,
            backend=self.backend_name,
        )


# ---------------------------------------------------------------------------
# Subprocess backend (degraded fallback)
# ---------------------------------------------------------------------------


class SubprocessBackend:
    """Degraded fallback: subprocess in a fresh tempdir.

    NOT isolated. NOT a sandbox. Use only for trusted fixture content,
    or for CI where Docker is not available. Always records
    backend="subprocess" so scoring can refuse to count it.
    """

    backend_name = "subprocess"

    def run(
        self,
        script: str,
        config: SandboxConfig,
        files: dict[str, str] | None = None,
    ) -> SandboxResult:
        import time

        config.validate()
        files = files or {}
        for name in files:
            if "/" in name or name.startswith(".") or name in {"", "."}:
                return SandboxResult(
                    ok=False,
                    exit_code=None,
                    stdout="",
                    stderr="",
                    duration_seconds=0.0,
                    backend=self.backend_name,
                    sandbox_violation=True,
                    error=f"file name fails validation: {name!r}",
                )

        env: dict[str, str] = {}
        for k in config.env_allowlist:
            if k in os.environ:
                env[k] = os.environ[k]
        for k, v in config.extra_env.items():
            env[k] = v

        start = time.monotonic()
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            for name, content in files.items():
                (td_path / name).write_text(content)
            (td_path / "_entry.py").write_text(script)
            try:
                r = subprocess.run(
                    [sys.executable, "_entry.py"],
                    cwd=td_path,
                    capture_output=True,
                    text=True,
                    timeout=config.timeout_seconds,
                    shell=False,
                    check=False,
                    env=env,
                )
            except subprocess.TimeoutExpired:
                return SandboxResult(
                    ok=False,
                    exit_code=None,
                    stdout="",
                    stderr="",
                    duration_seconds=time.monotonic() - start,
                    backend=self.backend_name,
                    timed_out=True,
                    error="timeout",
                )
            return SandboxResult(
                ok=r.returncode == 0,
                exit_code=r.returncode,
                stdout=r.stdout,
                stderr=r.stderr,
                duration_seconds=time.monotonic() - start,
                backend=self.backend_name,
            )


# ---------------------------------------------------------------------------
# selection
# ---------------------------------------------------------------------------


def available_backend(prefer_docker: bool = True) -> SandboxBackend:
    """Return the strongest backend available on this host.

    Order: DockerBackend if `prefer_docker` and `docker` binary is on
    PATH *and* the daemon responds to a probe; otherwise
    SubprocessBackend.
    """
    if prefer_docker and shutil.which("docker") is not None:
        try:
            probe = subprocess.run(
                [shutil.which("docker") or "docker", "version", "--format", "ok"],
                capture_output=True,
                text=True,
                timeout=2,
                shell=False,
                check=False,
            )
            if probe.returncode == 0:
                return DockerBackend()
        except (RuntimeError, OSError, subprocess.TimeoutExpired):
            pass
    return SubprocessBackend()


def run_in_sandbox(
    script: str,
    config: SandboxConfig | None = None,
    files: dict[str, str] | None = None,
    backend: SandboxBackend | None = None,
) -> SandboxResult:
    """Convenience: run `script` against the strongest backend available.

    Pass a `backend` explicitly to pin one (tests do this).
    """
    cfg = config or SandboxConfig()
    b = backend or available_backend()
    return b.run(script, cfg, files=files)


# ---------------------------------------------------------------------------
# internals
# ---------------------------------------------------------------------------


def _bootstrap_program(files: dict[str, str], script: str) -> str:
    """Build a single Python program that materializes files into /work
    and then exec()s the user script. Used by DockerBackend, which has
    no host mount to write files into.
    """
    import json

    files_json = json.dumps(files)
    return (
        "import json, os\n"
        f"_files = json.loads({files_json!r})\n"
        "for _name, _content in _files.items():\n"
        "    with open(_name, 'w') as _fh:\n"
        "        _fh.write(_content)\n"
        f"exec(compile({script!r}, '<sandbox-entry>', 'exec'), {{'__name__': '__main__'}})\n"
    )
