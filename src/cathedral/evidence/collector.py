"""Evidence collector — orchestrates fetch + verify + filter.

Implements issue #2 acceptance criteria. Partial-failure policy:
- Manifest signature/hash failure or agent_id mismatch → fatal (claim rejected)
- Per-record run/artifact/usage failure → drop the record, count for telemetry
"""

from __future__ import annotations

from datetime import UTC, datetime

import structlog
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from cathedral.evidence import verify
from cathedral.evidence.fetch import FetchError, MissingRecordError, PolarisFetcher
from cathedral.evidence.filter import filter_usage
from cathedral.types import (
    EvidenceBundle,
    PolarisAgentClaim,
    PolarisArtifactRecord,
    PolarisRunRecord,
    PolarisUsageRecord,
)

logger = structlog.get_logger(__name__)


class CollectionError(Exception):
    """Bundle could not be built; the claim should be rejected."""


class EvidenceCollector:
    def __init__(self, fetcher: PolarisFetcher, polaris_pubkey: Ed25519PublicKey) -> None:
        self.fetcher = fetcher
        self.pubkey = polaris_pubkey

    async def collect(self, claim: PolarisAgentClaim) -> EvidenceBundle:
        try:
            manifest = await self.fetcher.fetch_manifest(claim.polaris_agent_id)
        except MissingRecordError as e:
            raise CollectionError(f"manifest missing: {e}") from e
        except FetchError as e:
            raise CollectionError(f"manifest fetch failed: {e}") from e

        try:
            verify.verify_manifest(manifest, self.pubkey)
        except verify.VerificationError as e:
            raise CollectionError(str(e)) from e

        if manifest.polaris_agent_id != claim.polaris_agent_id:
            raise CollectionError(
                f"manifest mismatch: claim {claim.polaris_agent_id} "
                f"vs manifest {manifest.polaris_agent_id}"
            )

        runs: list[PolarisRunRecord] = []
        for run_id in claim.polaris_run_ids:
            try:
                run_rec = await self.fetcher.fetch_run(run_id)
                verify.verify_run(run_rec, self.pubkey)
                runs.append(run_rec)
            except (MissingRecordError, FetchError, verify.VerificationError) as e:
                logger.info("dropped_run", run_id=run_id, reason=str(e))

        # Artifact fetching is the legacy path: cards live on Cathedral
        # now and miners submit them inline via `claim.card_payload`.
        # Only fall back to fetching artifacts from Polaris if the
        # claim DOESN'T carry an inline payload — preserves backward
        # compatibility with earlier-spec miners. New deployments will
        # always set `card_payload` and skip the fetch entirely.
        artifacts: list[PolarisArtifactRecord] = []
        if claim.card_payload is None:
            for artifact_id in claim.polaris_artifact_ids:
                try:
                    art_rec = await self.fetcher.fetch_artifact(artifact_id)
                    verify.verify_artifact_record(art_rec, self.pubkey)
                    raw = await self.fetcher.fetch_artifact_bytes(art_rec.content_url)
                    verify.verify_artifact_bytes(art_rec, raw)
                    artifacts.append(art_rec)
                except (MissingRecordError, FetchError, verify.VerificationError) as e:
                    logger.info("dropped_artifact", artifact_id=artifact_id, reason=str(e))

        try:
            raw_usage = await self.fetcher.fetch_usage(claim.polaris_agent_id)
        except FetchError as e:
            raise CollectionError(f"usage fetch failed: {e}") from e

        verified_usage: list[PolarisUsageRecord] = []
        for u in raw_usage:
            try:
                verify.verify_usage(u, self.pubkey)
                verified_usage.append(u)
            except verify.VerificationError as e:
                logger.info("dropped_usage", usage_id=u.usage_id, reason=str(e))

        kept_usage = filter_usage(verified_usage, claim.owner_wallet)
        filtered_count = len(verified_usage) - len(kept_usage)

        return EvidenceBundle(
            manifest=manifest,
            runs=runs,
            artifacts=artifacts,
            usage=kept_usage,
            verified_at=datetime.now(UTC),
            filtered_usage_count=filtered_count,
        )
