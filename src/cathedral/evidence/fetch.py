"""Polaris HTTPS fetcher.

The Protocol class lets tests stub fetches with an in-memory keypair while
production wraps `httpx.AsyncClient`.
"""

from __future__ import annotations

from typing import Protocol, TypeVar

import httpx
from pydantic import BaseModel

from cathedral.types import (
    PolarisArtifactRecord,
    PolarisManifest,
    PolarisRunRecord,
    PolarisUsageRecord,
)

ModelT = TypeVar("ModelT", bound=BaseModel)


class FetchError(Exception):
    """Network or decode error."""


class MissingRecordError(Exception):
    """Polaris returned 404."""


class PolarisFetcher(Protocol):
    async def fetch_manifest(self, polaris_agent_id: str) -> PolarisManifest: ...
    async def fetch_run(self, run_id: str) -> PolarisRunRecord: ...
    async def fetch_artifact(self, artifact_id: str) -> PolarisArtifactRecord: ...
    async def fetch_artifact_bytes(self, url: str) -> bytes: ...
    async def fetch_usage(self, polaris_agent_id: str) -> list[PolarisUsageRecord]: ...


class HttpPolarisFetcher:
    """Production fetcher backed by httpx."""

    def __init__(self, base_url: str, timeout_secs: float = 20.0) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout_secs,
            headers={"User-Agent": "cathedral-validator/0.1"},
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def fetch_manifest(self, polaris_agent_id: str) -> PolarisManifest:
        return await self._fetch_one(f"/v1/agents/{polaris_agent_id}/manifest", PolarisManifest)

    async def fetch_run(self, run_id: str) -> PolarisRunRecord:
        return await self._fetch_one(f"/v1/runs/{run_id}", PolarisRunRecord)

    async def fetch_artifact(self, artifact_id: str) -> PolarisArtifactRecord:
        return await self._fetch_one(f"/v1/artifacts/{artifact_id}", PolarisArtifactRecord)

    async def fetch_artifact_bytes(self, url: str) -> bytes:
        try:
            r = await self._client.get(url)
            r.raise_for_status()
            return r.content
        except httpx.HTTPError as e:
            raise FetchError(str(e)) from e

    async def fetch_usage(self, polaris_agent_id: str) -> list[PolarisUsageRecord]:
        try:
            r = await self._client.get(f"/v1/agents/{polaris_agent_id}/usage")
            r.raise_for_status()
            return [PolarisUsageRecord.model_validate(x) for x in r.json()]
        except httpx.HTTPError as e:
            raise FetchError(str(e)) from e

    async def _fetch_one(self, path: str, model: type[ModelT]) -> ModelT:
        try:
            r = await self._client.get(path)
        except httpx.HTTPError as e:
            raise FetchError(str(e)) from e
        if r.status_code == 404:
            raise MissingRecordError(path)
        r.raise_for_status()
        return model.model_validate(r.json())
