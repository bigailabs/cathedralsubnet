"""Hard contract: the validator path must NEVER invoke patch-runner code.

This test simulates the validator pull loop's hot path: take a
signed v4 row, verify it, extract the score. While the verification
runs we monkeypatch every entry point into the publisher-side
oracle / subprocess machinery and assert none of them are called.

Why this matters: validators run on untrusted hosts at the edge of
the subnet. If a validator ever executes miner-supplied patch code,
the security posture collapses. The architectural rule is
``validators only verify signed rows``; this test pins it.
"""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from typing import Any

import pytest
from nacl.signing import SigningKey

import cathedral.v4.oracle.patch_runner as patch_runner_module
from cathedral.v4 import (
    ValidationPayload,
    build_signed_v4_row,
    verify_v4_row,
)


class _MockEvalSigner:
    def __init__(self, signing_key: SigningKey) -> None:
        self._sk = signing_key


@pytest.fixture
def signed_v4_row() -> tuple[dict[str, Any], Any]:
    sk = SigningKey.generate()
    signer = _MockEvalSigner(sk)
    payload = ValidationPayload(
        task_id="v4t_val_001",
        difficulty_tier="bronze",
        language="python",
        injected_fault_type="x",
        winning_patch="",
        trajectories=[],
        deterministic_hash="0" * 64,
    )
    row = build_signed_v4_row(
        eval_run_id="run_validator_test",
        miner_hotkey="5DfHt...validator_test",
        payload=payload,
        weighted_score=0.42,
        outcome="SUCCESS",
        total_turns=3,
        ran_at_iso=datetime.now(UTC).isoformat(),
        signer=signer,
    )
    return row, sk.verify_key


def test_validator_verifies_without_invoking_patch_runner(
    signed_v4_row: tuple[dict[str, Any], Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify path stays pure-CPU; oracle entry points are unreachable."""
    row, pubkey = signed_v4_row

    call_log: list[str] = []

    def _bad_run_patch(*_a: Any, **_kw: Any) -> Any:
        call_log.append("run_patch_against_hidden_test")
        raise AssertionError("validator must NEVER call patch runner")

    def _bad_popen(*_a: Any, **_kw: Any) -> Any:
        call_log.append("subprocess.Popen")
        raise AssertionError("validator must NEVER spawn a subprocess")

    def _bad_run(*_a: Any, **_kw: Any) -> Any:
        call_log.append("subprocess.run")
        raise AssertionError("validator must NEVER spawn a subprocess")

    monkeypatch.setattr(patch_runner_module, "run_patch_against_hidden_test", _bad_run_patch)
    monkeypatch.setattr(subprocess, "Popen", _bad_popen)
    monkeypatch.setattr(subprocess, "run", _bad_run)

    verified, score = verify_v4_row(row, publisher_pubkey=pubkey)
    assert verified is True
    assert score == pytest.approx(0.42)
    assert call_log == [], f"validator path invoked forbidden machinery: {call_log}"


def test_verify_module_does_not_import_oracle() -> None:
    """Source-level check: cathedral.v4.verify must not pull the oracle.

    A future refactor that adds ``from cathedral.v4.oracle ...`` into
    ``verify.py`` would silently open the door to the validator
    importing patch-runner machinery (and its subprocess /
    rlimit / unshare deps). Pin it at the source level.
    """
    import inspect

    from cathedral.v4 import verify as verify_module

    src = inspect.getsource(verify_module)
    assert "from cathedral.v4.oracle" not in src
    assert "import subprocess" not in src
    assert "patch_runner" not in src
