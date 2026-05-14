"""Sandbox runner for coding jobs.

Two backends, same shape:
  - DockerBackend: real isolation. `--network=none`, read-only root,
    env allowlist, resource limits, no host mounts. Use this in
    production.
  - SubprocessBackend: degraded fallback for CI / dev boxes without
    Docker. NOT isolated. Run only against trusted fixture content.

`SandboxResult.backend` records which one ran; the trajectory carries
this forward so downstream scoring can refuse training rows that came
from the degraded backend if it wants to.
"""

from cathedral.v3.sandbox.runner import (
    DockerBackend,
    SandboxBackend,
    SandboxConfig,
    SandboxResult,
    SubprocessBackend,
    available_backend,
    run_in_sandbox,
)

__all__ = [
    "DockerBackend",
    "SandboxBackend",
    "SandboxConfig",
    "SandboxResult",
    "SubprocessBackend",
    "available_backend",
    "run_in_sandbox",
]
