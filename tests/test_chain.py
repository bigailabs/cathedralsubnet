import pytest

from cathedral.chain import MockChain, normalize
from cathedral.chain.client import (
    Metagraph,
    MinerNode,
    WeightStatus,
    _classify_error,
    network_endpoint,
)


def test_network_endpoint_known() -> None:
    assert network_endpoint("finney").startswith("wss://")
    assert network_endpoint("test").startswith("wss://")
    assert network_endpoint("local").startswith("ws://")


def test_network_endpoint_unknown_raises() -> None:
    with pytest.raises(ValueError):
        network_endpoint("nope")


def test_classify_error_stake_fragments() -> None:
    assert _classify_error("validator permit not held") is WeightStatus.BLOCKED_BY_STAKE
    assert _classify_error("not enough stake") is WeightStatus.BLOCKED_BY_STAKE
    assert _classify_error("min_allowed_weights") is WeightStatus.BLOCKED_BY_STAKE


def test_classify_error_other() -> None:
    assert _classify_error("rpc timeout") is WeightStatus.BLOCKED_BY_TRANSACTION_ERROR
    assert _classify_error("") is WeightStatus.BLOCKED_BY_TRANSACTION_ERROR


@pytest.mark.asyncio
async def test_mock_chain_is_registered_default() -> None:
    chain = MockChain(
        Metagraph(block=1, miners=(MinerNode(uid=0, hotkey="5h", last_update_block=1),))
    )
    assert await chain.is_registered() is True
    assert (await chain.metagraph()).block == 1


@pytest.mark.asyncio
async def test_mock_chain_set_weights_records_input() -> None:
    chain = MockChain()
    status = await chain.set_weights([(1, 0.5), (2, 0.5)])
    assert status is WeightStatus.HEALTHY
    assert chain.last_weights == [(1, 0.5), (2, 0.5)]


def test_normalize_basic() -> None:
    out = normalize([(0, 1.0), (1, 1.0), (2, 2.0)])
    total = sum(w for _, w in out)
    assert abs(total - 1.0) < 1e-6
