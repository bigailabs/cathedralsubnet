import pytest

from cathedral.evidence.collector import CollectionError, EvidenceCollector
from cathedral.types import ConsumerClass, PolarisAgentClaim
from tests.conftest import (
    StubFetcher,
    make_signed_artifact,
    make_signed_manifest,
    make_signed_run,
    make_signed_usage,
)


def _claim() -> PolarisAgentClaim:
    return PolarisAgentClaim(
        miner_hotkey="5F",
        owner_wallet="wallet_owner",
        work_unit="card:eu-ai-act",
        polaris_agent_id="agt_1",
        polaris_run_ids=["run_1"],
        polaris_artifact_ids=["art_1"],
    )


@pytest.mark.asyncio
async def test_happy_path_returns_bundle(keypair) -> None:
    sk, pk = keypair
    f = StubFetcher()
    f.manifests["agt_1"] = make_signed_manifest(sk)
    f.runs["run_1"] = make_signed_run(sk, "run_1")

    body = b"hello"
    art = make_signed_artifact(sk, "art_1", content=body, report={"summary": "ok"})
    f.artifacts["art_1"] = art
    f.artifact_bytes[art.content_url] = body
    f.usage["agt_1"] = [
        make_signed_usage(sk, "u1"),
        make_signed_usage(sk, "u2", flagged=True),
        make_signed_usage(sk, "u3", consumer=ConsumerClass.CREATOR),
    ]

    bundle = await EvidenceCollector(f, pk).collect(_claim())
    assert len(bundle.runs) == 1
    assert len(bundle.artifacts) == 1
    assert len(bundle.usage) == 1
    assert bundle.filtered_usage_count == 2


@pytest.mark.asyncio
async def test_missing_manifest_rejects(keypair) -> None:
    _, pk = keypair
    f = StubFetcher()
    with pytest.raises(CollectionError):
        await EvidenceCollector(f, pk).collect(_claim())


@pytest.mark.asyncio
async def test_artifact_hash_mismatch_drops_artifact(keypair) -> None:
    sk, pk = keypair
    f = StubFetcher()
    f.manifests["agt_1"] = make_signed_manifest(sk)
    f.runs["run_1"] = make_signed_run(sk, "run_1")
    art = make_signed_artifact(sk, "art_1", content=b"original", report={"x": 1})
    f.artifacts["art_1"] = art
    f.artifact_bytes[art.content_url] = b"tampered"
    f.usage["agt_1"] = []
    bundle = await EvidenceCollector(f, pk).collect(_claim())
    assert bundle.artifacts == []
