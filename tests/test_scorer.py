from datetime import UTC, datetime, timedelta

from cathedral.cards.registry import CardRegistry
from cathedral.cards.score import score_card
from cathedral.types import Source, SourceClass
from tests.conftest import make_card


def test_all_official_sources_score_high() -> None:
    parts = score_card(make_card(), CardRegistry.baseline().lookup("eu-ai-act"))
    assert parts.source_quality > 0.9
    assert parts.weighted() > 0.4


def test_freshness_decays_with_age() -> None:
    fresh = score_card(make_card(last_refreshed_at=datetime.now(UTC)))
    stale = score_card(make_card(last_refreshed_at=datetime.now(UTC) - timedelta(hours=200)))
    assert fresh.freshness > stale.freshness
    assert stale.freshness == 0.0


def test_secondary_only_sources_score_low_quality() -> None:
    src = Source(
        url="https://example.com/blog",
        **{"class": SourceClass.SECONDARY_ANALYSIS},
        fetched_at=datetime.now(UTC),
        status=200,
        content_hash="d",
    )
    parts = score_card(make_card(citations=[src]))
    assert parts.source_quality == 0.0
