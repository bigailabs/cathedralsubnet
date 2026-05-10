"""Miner-side claim submission."""

from __future__ import annotations

import os
from datetime import UTC, datetime

import httpx

from cathedral.config import MinerSettings
from cathedral.types import PolarisAgentClaim


class SubmitError(Exception):
    pass


async def submit_claim(
    settings: MinerSettings,
    *,
    work_unit: str,
    polaris_agent_id: str,
    polaris_deployment_id: str | None = None,
    polaris_run_ids: list[str] | None = None,
    polaris_artifact_ids: list[str] | None = None,
) -> int:
    bearer = os.environ.get(settings.validator_bearer_env)
    if not bearer:
        raise SubmitError(f"missing bearer env {settings.validator_bearer_env}")

    claim = PolarisAgentClaim(
        miner_hotkey=settings.miner_hotkey,
        owner_wallet=settings.owner_wallet,
        work_unit=work_unit,
        polaris_agent_id=polaris_agent_id,
        polaris_deployment_id=polaris_deployment_id,
        polaris_run_ids=polaris_run_ids or [],
        polaris_artifact_ids=polaris_artifact_ids or [],
        submitted_at=datetime.now(UTC),
    )

    url = f"{settings.validator_url.rstrip('/')}/v1/claim"
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            r = await client.post(
                url,
                headers={"Authorization": f"Bearer {bearer}"},
                json=claim.model_dump(mode="json"),
            )
        except httpx.HTTPError as e:
            raise SubmitError(f"transport: {e}") from e

    if r.status_code >= 400:
        raise SubmitError(f"validator rejected claim: {r.status_code} {r.text}")
    body = r.json()
    return int(body.get("id", 0))
