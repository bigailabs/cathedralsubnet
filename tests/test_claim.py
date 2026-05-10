from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from cathedral.types import ClaimVersion, PolarisAgentClaim


def _claim(**overrides: object) -> PolarisAgentClaim:
    base = dict(
        miner_hotkey="5F",
        owner_wallet="5G",
        work_unit="card:eu-ai-act",
        polaris_agent_id="agt_01H",
        polaris_run_ids=["run_1"],
        polaris_artifact_ids=["art_1"],
        submitted_at=datetime.now(UTC),
    )
    base.update(overrides)
    return PolarisAgentClaim(**base)  # type: ignore[arg-type]


def test_default_shape_is_valid() -> None:
    c = _claim()
    assert c.version is ClaimVersion.V1
    assert c.type == "cathedral.polaris_agent_claim.v1"


def test_missing_polaris_agent_id_fails() -> None:
    with pytest.raises(ValidationError):
        _claim(polaris_agent_id="")


def test_missing_work_unit_fails() -> None:
    with pytest.raises(ValidationError):
        _claim(work_unit="")


def test_json_roundtrip() -> None:
    c = _claim()
    j = c.model_dump_json()
    back = PolarisAgentClaim.model_validate_json(j)
    assert back.polaris_agent_id == c.polaris_agent_id
    assert back.polaris_run_ids == c.polaris_run_ids
