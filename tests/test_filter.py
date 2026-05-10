from datetime import UTC, datetime

from cathedral.evidence.filter import filter_usage
from cathedral.types import ConsumerClass, PolarisUsageRecord


def _u(**overrides: object) -> PolarisUsageRecord:
    base = dict(
        usage_id="u",
        polaris_agent_id="a",
        consumer=ConsumerClass.EXTERNAL,
        consumer_wallet="other",
        used_at=datetime.now(UTC),
        flagged=False,
        refunded=False,
        signature="x",
    )
    base.update(overrides)
    return PolarisUsageRecord(**base)  # type: ignore[arg-type]


def test_drops_flagged_refunded_and_non_external() -> None:
    rows = [
        _u(),
        _u(flagged=True),
        _u(refunded=True),
        _u(consumer=ConsumerClass.CREATOR),
        _u(consumer=ConsumerClass.PLATFORM),
        _u(consumer=ConsumerClass.TEST),
        _u(consumer=ConsumerClass.SELF_LOOP),
    ]
    kept = filter_usage(rows, owner_wallet="owner")
    assert len(kept) == 1


def test_self_loop_by_consumer_wallet_match() -> None:
    rows = [_u(consumer_wallet="owner"), _u(consumer_wallet="other")]
    kept = filter_usage(rows, owner_wallet="owner")
    assert len(kept) == 1
    assert kept[0].consumer_wallet == "other"
