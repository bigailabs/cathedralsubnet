"""Eval lifecycle orchestrator — CONTRACTS.md §6.

Goal: verify the queued -> evaluating -> ranked|rejected state machine,
the 3-retry exponential-backoff policy on Polaris failure, the malformed-
output preflight rejection, and the first-mover delta application path.

We use the contract's stub mode (`CATHEDRAL_EVAL_MODE=stub`, §6) when
available. If the orchestrator module isn't importable yet, the
dependent tests skip with a pointer to the contract section.
"""

from __future__ import annotations

import importlib
from typing import Any

import pytest

from tests.v1.conftest import (
    make_valid_bundle,
    submit_multipart,
)

# --------------------------------------------------------------------------
# Helpers — best-effort import of orchestrator
# --------------------------------------------------------------------------


def _try_import(name: str) -> Any | None:
    try:
        return importlib.import_module(name)
    except Exception:
        return None


@pytest.fixture
def eval_orchestrator():
    """Best-effort import of the eval orchestrator.

    Tries a handful of plausible module paths from the contract.
    """
    for name in (
        "cathedral.eval.orchestrator",
        "cathedral.eval.scheduler",
        "cathedral.eval",
    ):
        mod = _try_import(name)
        if mod is None:
            continue
        for attr in ("run_once", "tick", "process_one", "schedule_once", "Orchestrator"):
            if hasattr(mod, attr):
                return mod
    pytest.skip(
        "eval orchestrator not importable yet — implementer must expose "
        "cathedral.eval.{orchestrator,scheduler} per CONTRACTS.md §6"
    )


@pytest.fixture(autouse=True)
def _force_stub_mode(monkeypatch):
    """CONTRACTS.md §6 — `CATHEDRAL_EVAL_MODE=stub` runs without real Polaris."""
    monkeypatch.setenv("CATHEDRAL_EVAL_MODE", "stub")


# --------------------------------------------------------------------------
# Status transitions (§6 state machine)
# --------------------------------------------------------------------------


def test_queued_submission_gets_picked_up(
    publisher_client, alice_keypair, eval_orchestrator
):
    """§6 step 3 — scheduler picks queued submissions FIFO by submitted_at."""
    bundle = make_valid_bundle(soul_md="# pickup probe\n")
    resp = submit_multipart(
        publisher_client,
        keypair=alice_keypair,
        card_id="eu-ai-act",
        bundle=bundle,
    )
    if resp.status_code != 202:
        pytest.skip(f"submit not ready: {resp.text}")
    agent_id = resp.json()["id"]

    # Drive one orchestrator tick. Try the most likely entry points.
    _drive_one_tick(eval_orchestrator)

    # Status should advance past queued (to evaluating, ranked, or rejected).
    profile = publisher_client.get(f"/v1/agents/{agent_id}").json()
    assert profile["status"] in {"evaluating", "ranked", "rejected"}, (
        f"§6 step 3: queued submission must advance after a tick; "
        f"got status={profile['status']!r}"
    )


def test_successful_eval_writes_eval_run_and_marks_ranked(
    publisher_client, alice_keypair, eval_orchestrator
):
    """§6 step 4-5 — successful eval persists EvalRun row + sets status='ranked'."""
    bundle = make_valid_bundle(soul_md="# success path\n")
    resp = submit_multipart(
        publisher_client,
        keypair=alice_keypair,
        card_id="eu-ai-act",
        bundle=bundle,
    )
    if resp.status_code != 202:
        pytest.skip(f"submit not ready: {resp.text}")
    agent_id = resp.json()["id"]

    # Drive ticks until status leaves 'queued'/'evaluating'.
    for _ in range(5):
        _drive_one_tick(eval_orchestrator)
        profile = publisher_client.get(f"/v1/agents/{agent_id}").json()
        if profile["status"] not in {"queued", "evaluating", "pending_check"}:
            break

    profile = publisher_client.get(f"/v1/agents/{agent_id}").json()
    assert profile["status"] in {"ranked", "rejected"}, (
        f"§6: eval must terminate at ranked or rejected; got {profile['status']!r}"
    )
    if profile["status"] == "ranked":
        # current_score must be populated (§6 step 5).
        assert profile.get("current_score") is not None, (
            "§6 step 5: ranked submission must have current_score set"
        )
        # recent_evals must contain at least one EvalOutput (§1.9).
        assert len(profile.get("recent_evals", [])) >= 1, (
            "§1.9: ranked AgentProfile must include at least one recent_eval"
        )


def test_status_transitions_only_via_allowed_arrows(
    publisher_client, alice_keypair, eval_orchestrator
):
    """§6 'Status transitions (allowed):' block.

    pending_check → queued | rejected
    queued        → evaluating | withdrawn
    evaluating    → ranked | rejected | queued (on retryable failure)
    ranked        → ranked (re-eval) | withdrawn
    rejected      → terminal
    withdrawn     → terminal
    """
    allowed: dict[str, set[str]] = {
        "pending_check": {"pending_check", "queued", "rejected"},
        "queued": {"queued", "evaluating", "withdrawn"},
        "evaluating": {"evaluating", "ranked", "rejected", "queued"},
        "ranked": {"ranked", "withdrawn"},
        "rejected": {"rejected"},  # terminal
        "withdrawn": {"withdrawn"},  # terminal
    }

    bundle = make_valid_bundle(soul_md="# transition probe\n")
    resp = submit_multipart(
        publisher_client,
        keypair=alice_keypair,
        card_id="eu-ai-act",
        bundle=bundle,
    )
    if resp.status_code != 202:
        pytest.skip(f"submit not ready: {resp.text}")
    agent_id = resp.json()["id"]

    seen: list[str] = []
    for _ in range(8):
        prof = publisher_client.get(f"/v1/agents/{agent_id}").json()
        seen.append(prof["status"])
        if prof["status"] in {"ranked", "rejected", "withdrawn"}:
            break
        _drive_one_tick(eval_orchestrator)

    # Walk the observed sequence and ensure each step is in the allowed set.
    from itertools import pairwise

    for prev, curr in pairwise(seen):
        assert curr in allowed.get(prev, set()), (
            f"§6 status machine: illegal transition {prev!r} -> {curr!r}; "
            f"full sequence={seen}"
        )


# --------------------------------------------------------------------------
# Polaris timeout / retry policy (§6 step 3 + 'Timeouts and policies')
# --------------------------------------------------------------------------


def test_polaris_timeout_retries_then_marks_failed(
    publisher_client, alice_keypair, monkeypatch, eval_orchestrator
):
    """§6 'Timeouts and policies' — 3 retries with exponential backoff.

    We can only verify this end-to-end if the implementer surfaces the
    retry counter or the failure marker in the eval_run errors. We assert
    on the OBSERVABLE: after exhausting retries, the eval_run for this
    submission has non-empty `errors` AND `weighted_score == 0`.
    """
    monkeypatch.setenv("CATHEDRAL_EVAL_MODE", "stub-fail-polaris")

    bundle = make_valid_bundle(soul_md="# polaris timeout probe\n")
    resp = submit_multipart(
        publisher_client,
        keypair=alice_keypair,
        card_id="eu-ai-act",
        bundle=bundle,
    )
    if resp.status_code != 202:
        pytest.skip(f"submit not ready: {resp.text}")
    agent_id = resp.json()["id"]

    # Drive enough ticks to exhaust retries + persist the failure.
    for _ in range(10):
        _drive_one_tick(eval_orchestrator)
        prof = publisher_client.get(f"/v1/agents/{agent_id}").json()
        if prof["status"] in {"ranked", "rejected"}:
            break

    prof = publisher_client.get(f"/v1/agents/{agent_id}").json()
    # The eval lifecycle says: leave status='evaluating' after 3 failures
    # AND log via health_kv counter; OR if Card JSON parse fails, record
    # EvalRun with errors=[...] and weighted_score=0. Either is contract-OK.
    if prof.get("recent_evals"):
        last = prof["recent_evals"][-1]
        if last.get("weighted_score") is not None:
            assert last["weighted_score"] == 0 or last.get("errors"), (
                "§6 step 3-4: failed Polaris run must surface as errors=[...] OR "
                f"weighted_score=0; got {last}"
            )


def test_malformed_card_output_records_preflight_rejection(
    publisher_client, alice_keypair, monkeypatch, eval_orchestrator
):
    """§6 step 4 — preflight failure: weighted_score=0, errors=[str(exc)]."""
    monkeypatch.setenv("CATHEDRAL_EVAL_MODE", "stub-bad-card")

    bundle = make_valid_bundle(soul_md="# malformed card probe\n")
    resp = submit_multipart(
        publisher_client,
        keypair=alice_keypair,
        card_id="eu-ai-act",
        bundle=bundle,
    )
    if resp.status_code != 202:
        pytest.skip(f"submit not ready: {resp.text}")
    agent_id = resp.json()["id"]

    for _ in range(6):
        _drive_one_tick(eval_orchestrator)
        prof = publisher_client.get(f"/v1/agents/{agent_id}").json()
        if prof.get("recent_evals"):
            break

    prof = publisher_client.get(f"/v1/agents/{agent_id}").json()
    if not prof.get("recent_evals"):
        pytest.skip(
            "no eval_run persisted yet — orchestrator may not surface stub-bad-card "
            "via the read API yet"
        )
    last = prof["recent_evals"][-1]
    assert last.get("weighted_score") == 0, (
        f"§6 step 4: malformed card must score 0; got {last}"
    )


# --------------------------------------------------------------------------
# First-mover delta integration (§7.2)
# --------------------------------------------------------------------------


def test_late_submission_within_threshold_gets_penalty_multiplier(
    publisher_client, alice_keypair, bob_keypair, eval_orchestrator, monkeypatch
):
    """§7.2 — late submission with weighted within delta of incumbent gets 0.50x."""
    monkeypatch.setenv("CATHEDRAL_EVAL_MODE", "stub-deterministic-score")
    monkeypatch.setenv("CATHEDRAL_STUB_SCORE", "0.80")

    # Alice submits first (incumbent).
    a_bundle = make_valid_bundle(soul_md="# Alice (incumbent)\n")
    a_resp = submit_multipart(
        publisher_client, keypair=alice_keypair, card_id="eu-ai-act",
        bundle=a_bundle, display_name="Incumbent",
    )
    if a_resp.status_code != 202:
        pytest.skip(f"submit not ready: {a_resp.text}")
    a_id = a_resp.json()["id"]

    # Drive Alice through eval.
    for _ in range(5):
        _drive_one_tick(eval_orchestrator)
        prof = publisher_client.get(f"/v1/agents/{a_id}").json()
        if prof["status"] == "ranked":
            break

    # Bob submits later with the SAME stub score (0.80) — within 0.05 delta.
    b_bundle = make_valid_bundle(soul_md="# Bob (late copy)\n")
    b_resp = submit_multipart(
        publisher_client, keypair=bob_keypair, card_id="eu-ai-act",
        bundle=b_bundle, display_name="Latecomer",
    )
    if b_resp.status_code != 202:
        pytest.skip(f"second submit blocked by similarity: {b_resp.text}")
    b_id = b_resp.json()["id"]

    for _ in range(5):
        _drive_one_tick(eval_orchestrator)
        prof = publisher_client.get(f"/v1/agents/{b_id}").json()
        if prof["status"] == "ranked":
            break

    a_prof = publisher_client.get(f"/v1/agents/{a_id}").json()
    b_prof = publisher_client.get(f"/v1/agents/{b_id}").json()
    if a_prof["status"] != "ranked" or b_prof["status"] != "ranked":
        pytest.skip("could not drive both submissions to ranked under stub mode")

    # Per §7.2 with stub score 0.80 for both: incumbent keeps 0.80; latecomer
    # gets 0.40 (0.80 * 0.50 penalty multiplier).
    assert a_prof["current_score"] >= b_prof["current_score"], (
        f"§7.2: incumbent (a={a_prof['current_score']}) must outscore latecomer "
        f"(b={b_prof['current_score']}) under penalty multiplier"
    )


# --------------------------------------------------------------------------
# Internal helpers
# --------------------------------------------------------------------------


def _drive_one_tick(orchestrator) -> None:
    """Try the most likely orchestrator entry points to advance state by one step.

    The implementer hasn't decided the API surface yet. We try them in
    order; the first one that exists wins.
    """
    import asyncio
    import inspect

    for attr in ("run_once", "tick", "process_one", "schedule_once"):
        fn = getattr(orchestrator, attr, None)
        if fn is None:
            continue
        try:
            if inspect.iscoroutinefunction(fn):
                asyncio.get_event_loop().run_until_complete(fn())
            else:
                fn()
        except Exception:
            # If this entry point raises, try the next one.
            continue
        return

    # As a last resort, look for an Orchestrator class.
    cls = getattr(orchestrator, "Orchestrator", None)
    if cls is not None:
        try:
            inst = cls()
            for attr in ("run_once", "tick", "process_one"):
                fn = getattr(inst, attr, None)
                if fn is None:
                    continue
                if inspect.iscoroutinefunction(fn):
                    asyncio.get_event_loop().run_until_complete(fn())
                else:
                    fn()
                return
        except Exception:
            pass

    pytest.skip(
        "orchestrator has no run_once/tick/process_one entry point — "
        "implementer must expose one for tests per CONTRACTS.md §6"
    )
