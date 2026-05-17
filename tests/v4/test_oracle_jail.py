"""Filesystem jail regression tests for the publisher-side oracle.

Finding 1 of the PR #133 review: Fred's local probe showed that a
miner patch could read ``/etc/hosts`` during hidden-test import and
leak its contents through stdout. The fix is the Linux fs-jail
(``cathedral.v4.oracle.jail``); these tests pin two contracts:

  1. ``test_jail_blocks_etc_hosts_read`` -- on Linux the jail makes
     ``/etc/hosts`` unreachable from the child (ENOENT) and no
     plausible contents (``localhost`` / ``127.0.0.1``) appear in
     stdout / stderr. Skipped on non-Linux because the jail is a
     Linux-only mechanism; the production startup check refuses to
     run a publisher-class workload on macOS.
  2. ``test_assert_production_isolation_refuses_monkeypatch`` --
     when ``CATHEDRAL_V4_ENV=production`` and the resolved mode is
     ``monkeypatch_only`` (the macOS dev posture), the runner's
     startup check raises ``RuntimeError`` rather than silently
     downgrading. Verified on every platform because the function
     is platform-independent CPU code.

The jail test is structurally complete on macOS even though it is
skipped: it can be executed verbatim on a Linux CI runner (or any
Linux box) to confirm the regression is fixed.
"""

from __future__ import annotations

import os
import platform
import textwrap
from unittest import mock

import pytest

from cathedral.v4.oracle.patch_runner import (
    PATCH_RUNNER_TIMEOUT_SECONDS,
    PatchRunResult,
    assert_production_isolation,
    resolve_isolation_mode,
    run_patch_against_hidden_test,
)

# Files the jail must keep out of reach. Each entry: (path, sentinel
# substring we expect to see in the file's real contents on a
# typical host; used to detect leakage through stdout).
_HOST_FILES = [
    ("/etc/hosts", "localhost"),
    ("/etc/hosts", "127.0.0.1"),
    ("/etc/passwd", "root:"),
]


_LEAK_PROBE = textwrap.dedent(
    """
    import sys

    def _probe(path):
        try:
            with open(path) as f:
                data = f.read()
        except OSError as e:
            print(f"BLOCKED:{path}:errno={e.errno}")
            return None
        print(f"LEAKED:{path}:{data!r}")
        return data

    _probe('/etc/hosts')
    _probe('/etc/passwd')
    _probe(sys.executable + '/../../../etc/hosts')
    """
).strip()


@pytest.mark.skipif(platform.system() != "Linux", reason="jail requires Linux unshare(1) >= 2.36")
def test_jail_blocks_etc_hosts_read() -> None:
    """Mirror Fred's probe: a miner patch tries to read /etc/hosts.

    Inside the jail the read MUST fail (ENOENT / EACCES) and the
    file contents (``localhost``, ``127.0.0.1``, ``root:``) MUST
    NOT appear in stdout or stderr.
    """
    # Sanity: the host actually has the files we're trying to leak.
    # If not, the test would pass trivially even without isolation.
    assert os.path.exists("/etc/hosts"), "test precondition: host has /etc/hosts"

    # Build a hidden test that runs the leak probe at import time.
    hidden = (
        "import sys\nsys.path.insert(0, '.')\n" + _LEAK_PROBE + "\nassert True, 'probe complete'\n"
    )
    # Minimal workspace state: one stub file so the runner has
    # something to materialize.
    workspace = {"placeholder.py": "# v4 jail leak probe workspace\n"}

    # No miner patch needed for the leak vector; the probe lives in
    # the hidden test body. Run with a no-op patch (empty diff is
    # fine -- we don't care if patch_applied is False, the leak is
    # in hidden_test_code).
    noop_patch = ""
    result: PatchRunResult = run_patch_against_hidden_test(
        original_repo_state=workspace,
        patch_str=noop_patch,
        hidden_test_code=hidden,
        timeout_seconds=PATCH_RUNNER_TIMEOUT_SECONDS,
    )

    # The run completed under the jail.
    assert result.isolation_mode == "jailed", (
        f"expected jail isolation, got {result.isolation_mode!r}; "
        f"stdout={result.stdout[:200]!r} stderr={result.stderr[:200]!r}"
    )

    # No leak markers in either stream.
    assert "LEAKED:" not in result.stdout, f"jail leak via stdout: {result.stdout!r}"
    assert "LEAKED:" not in result.stderr, f"jail leak via stderr: {result.stderr!r}"

    for _path, sentinel in _HOST_FILES:
        assert sentinel not in result.stdout, (
            f"host-file sentinel {sentinel!r} leaked through stdout"
        )
        assert sentinel not in result.stderr, (
            f"host-file sentinel {sentinel!r} leaked through stderr"
        )

    # BLOCKED: markers SHOULD be present (the probe should have run
    # and seen ENOENT).
    assert "BLOCKED:" in result.stdout or "BLOCKED:" in result.stderr, (
        "leak probe never executed; isolation may be silently bypassed"
    )


def test_assert_production_isolation_refuses_monkeypatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Production startup check refuses to start under monkeypatch_only.

    Pattern: with CATHEDRAL_V4_ENV=production set, calling
    ``assert_production_isolation(mode='monkeypatch_only')`` raises
    RuntimeError. With the env var unset the same call is a no-op.
    """
    monkeypatch.setenv("CATHEDRAL_V4_ENV", "production")
    with pytest.raises(RuntimeError, match="isolation"):
        assert_production_isolation(mode="monkeypatch_only")

    # The error message names the env var and the resolved mode so
    # operators can debug without spelunking source.
    monkeypatch.setenv("CATHEDRAL_V4_ENV", "production")
    try:
        assert_production_isolation(mode="monkeypatch_only")
    except RuntimeError as exc:
        assert "CATHEDRAL_V4_ENV" in str(exc)
        assert "monkeypatch_only" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")

    # unshare_n_only is ALSO refused -- only jailed passes.
    with pytest.raises(RuntimeError, match="isolation"):
        assert_production_isolation(mode="unshare_n_only")

    # jailed passes.
    assert_production_isolation(mode="jailed")  # no raise


def test_assert_production_isolation_noop_when_env_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Outside production, weak isolation is allowed (dev convenience)."""
    monkeypatch.delenv("CATHEDRAL_V4_ENV", raising=False)
    # Every mode is acceptable when env is unset.
    assert_production_isolation(mode="monkeypatch_only")
    assert_production_isolation(mode="unshare_n_only")
    assert_production_isolation(mode="jailed")

    # And the default-resolved mode also passes.
    assert_production_isolation()


def test_run_patch_against_hidden_test_refuses_production_downgrade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The runner itself enforces the production check on every call.

    Even if the publisher worker never called
    ``assert_production_isolation`` at boot, the runner re-checks on
    every invocation so a misconfigured deployment cannot
    accumulate weak-isolation runs.
    """
    monkeypatch.setenv("CATHEDRAL_V4_ENV", "production")

    # Force the resolver to report a non-jailed mode so the check
    # fires regardless of host capability.
    with mock.patch(
        "cathedral.v4.oracle.patch_runner.resolve_isolation_mode",
        return_value="monkeypatch_only",
    ):
        with pytest.raises(RuntimeError, match="isolation"):
            run_patch_against_hidden_test(
                original_repo_state={"x.py": ""},
                patch_str="--- a/x.py\n+++ b/x.py\n",
                hidden_test_code="assert True",
            )


def test_resolve_isolation_mode_macos_returns_monkeypatch() -> None:
    """On macOS the resolver must return monkeypatch_only.

    Pin the platform contract: macOS dev hosts cannot get the fs
    jail, and the resolver should report so honestly. The runner
    relies on that honesty for the production startup check to bite.
    """
    if platform.system() != "Darwin":
        pytest.skip("macOS-only contract")
    assert resolve_isolation_mode() == "monkeypatch_only"
