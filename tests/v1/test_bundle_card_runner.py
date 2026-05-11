"""Unit tests for `BundleCardRunner` — Path A (BYO-compute).

The miner submits a Hermes-shaped zip containing `artifacts/last-card.json`
(or `card.json` at root). The publisher decrypts the bundle (handled by
the orchestrator), then `BundleCardRunner.run` parses the pre-baked Card
JSON and returns it for the scoring pipeline to grade.

These tests pin the contract:
- valid bundle -> parsed Card dict in `PolarisRunResult.output_card_json`
- missing card file -> `PolarisRunnerError` with a clear message
- malformed JSON -> `PolarisRunnerError` with a clear message
- non-object JSON root -> `PolarisRunnerError`
- alternate `card.json` path also works
- non-zip bytes -> `PolarisRunnerError`
"""

from __future__ import annotations

import importlib.util as _ilu
import io
import json
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

from cathedral.v1_types import EvalTask

# `cathedral.eval.__init__` triggers a circular import via
# scoring_pipeline -> publisher -> app -> orchestrator (PASS 1 HIGH-8 in
# the adversarial report). polaris_runner.py itself is cycle-free, so
# load it directly via importlib — same pattern as the exploit harness
# in tests/v1/exploits/polaris_unverified_output.py.
_PR_PATH = (
    Path(__file__).parent.parent.parent / "src/cathedral/eval/polaris_runner.py"
)
_spec = _ilu.spec_from_file_location("_polaris_runner_for_bundle_test", _PR_PATH)
assert _spec and _spec.loader
_pr = _ilu.module_from_spec(_spec)
sys.modules["_polaris_runner_for_bundle_test"] = _pr
_spec.loader.exec_module(_pr)
BundleCardRunner = _pr.BundleCardRunner
PolarisRunnerError = _pr.PolarisRunnerError


def _make_task() -> EvalTask:
    return EvalTask(
        card_id="eu-ai-act",
        epoch=1,
        round_index=0,
        prompt="summarise EU AI Act developments in the last 24h",
        sources=[],
        deadline_minutes=25,
    )


def _zip_with(files: dict[str, bytes | str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in files.items():
            if isinstance(data, str):
                zf.writestr(name, data)
            else:
                zf.writestr(name, data)
    return buf.getvalue()


def _valid_card_json() -> str:
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    return json.dumps(
        {
            "jurisdiction": "eu",
            "topic": "EU AI Act",
            "title": "EU AI Act ramp continues",
            "summary": "Real summary content, not a stub.",
            "what_changed": "GPAI obligations live since 2025-08-02.",
            "why_it_matters": "Providers face up to 3% turnover fines.",
            "action_notes": "Map deployments to Annex III categories.",
            "risks": "Penalties phase in alongside obligations.",
            "citations": [
                {
                    "url": "https://eur-lex.europa.eu/eli/reg/2024/1689/oj",
                    "class": "official_journal",
                    "fetched_at": now,
                    "status": 200,
                    "content_hash": "a" * 64,
                }
            ],
            "confidence": 0.72,
            "no_legal_advice": True,
            "last_refreshed_at": now,
            "refresh_cadence_hours": 24,
        },
        sort_keys=True,
    )


@pytest.mark.asyncio
async def test_bundle_card_runner_returns_parsed_card() -> None:
    """Happy path: zip contains artifacts/last-card.json → runner returns it."""
    card_json = _valid_card_json()
    bundle = _zip_with(
        {
            "soul.md": "# Soul\nI am a regulatory analyst.\n",
            "AGENTS.md": "Maintain eu-ai-act every 24h.\n",
            "artifacts/last-card.json": card_json,
        }
    )

    runner = BundleCardRunner()
    result = await runner.run(
        bundle_bytes=bundle,
        bundle_hash="deadbeef" * 8,
        task=_make_task(),
        miner_hotkey="5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY",
    )

    assert result.output_card_json == json.loads(card_json)
    # BYO-compute: no Polaris attribution, runner derives a synthetic run id.
    assert result.polaris_agent_id == ""
    assert result.polaris_run_id.startswith("bundle-eu-ai-act-")
    assert result.errors == []


@pytest.mark.asyncio
async def test_bundle_card_runner_accepts_alternate_card_json_path() -> None:
    """`card.json` at the root is also accepted as a friendlier alias."""
    card_json = _valid_card_json()
    bundle = _zip_with(
        {
            "soul.md": "# Soul\n",
            "card.json": card_json,
        }
    )

    runner = BundleCardRunner()
    result = await runner.run(
        bundle_bytes=bundle,
        bundle_hash="00" * 32,
        task=_make_task(),
        miner_hotkey="hk",
    )

    assert result.output_card_json["jurisdiction"] == "eu"


@pytest.mark.asyncio
async def test_bundle_card_runner_missing_file_raises() -> None:
    """Bundle without any card file → clear `PolarisRunnerError`."""
    bundle = _zip_with({"soul.md": "# Soul\n", "AGENTS.md": "no card\n"})

    runner = BundleCardRunner()
    with pytest.raises(PolarisRunnerError) as exc:
        await runner.run(
            bundle_bytes=bundle,
            bundle_hash="00" * 32,
            task=_make_task(),
            miner_hotkey="hk",
        )
    msg = str(exc.value)
    assert "missing card file" in msg
    assert "artifacts/last-card.json" in msg


@pytest.mark.asyncio
async def test_bundle_card_runner_malformed_json_raises() -> None:
    """Card file present but not valid JSON → `PolarisRunnerError`."""
    bundle = _zip_with(
        {
            "soul.md": "# Soul\n",
            "artifacts/last-card.json": "{not valid json,,,",
        }
    )

    runner = BundleCardRunner()
    with pytest.raises(PolarisRunnerError) as exc:
        await runner.run(
            bundle_bytes=bundle,
            bundle_hash="00" * 32,
            task=_make_task(),
            miner_hotkey="hk",
        )
    assert "malformed" in str(exc.value)


@pytest.mark.asyncio
async def test_bundle_card_runner_non_object_root_raises() -> None:
    """Card file is JSON but not an object → `PolarisRunnerError`."""
    bundle = _zip_with(
        {
            "soul.md": "# Soul\n",
            "artifacts/last-card.json": json.dumps(["not", "an", "object"]),
        }
    )

    runner = BundleCardRunner()
    with pytest.raises(PolarisRunnerError) as exc:
        await runner.run(
            bundle_bytes=bundle,
            bundle_hash="00" * 32,
            task=_make_task(),
            miner_hotkey="hk",
        )
    assert "must be a JSON object" in str(exc.value)


@pytest.mark.asyncio
async def test_bundle_card_runner_non_zip_raises() -> None:
    """Bundle bytes that don't decode as a zip → `PolarisRunnerError`."""
    runner = BundleCardRunner()
    with pytest.raises(PolarisRunnerError) as exc:
        await runner.run(
            bundle_bytes=b"this is not a zip file at all\n" * 10,
            bundle_hash="00" * 32,
            task=_make_task(),
            miner_hotkey="hk",
        )
    # Falls through the same "missing card file" path since we can't
    # crack the zip open. Either message is acceptable; just ensure
    # something clear is raised.
    assert "missing card file" in str(exc.value) or "not a valid zip" in str(exc.value)
