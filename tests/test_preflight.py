from datetime import UTC

import pytest

from cathedral.cards.preflight import (
    BrokenSourceError,
    LegalAdviceFramingError,
    MissingNoLegalAdviceMarkerError,
    NoCitationsError,
    preflight,
)
from tests.conftest import make_card


def test_no_citations_fails() -> None:
    with pytest.raises(NoCitationsError):
        preflight(make_card(citations=[]))


def test_missing_no_legal_advice_marker_fails() -> None:
    with pytest.raises(MissingNoLegalAdviceMarkerError):
        preflight(make_card(no_legal_advice=False))


def test_legal_advice_framing_fails() -> None:
    with pytest.raises(LegalAdviceFramingError):
        preflight(make_card(summary="We recommend that you do this immediately."))


def test_broken_source_fails() -> None:
    from datetime import datetime

    from cathedral.types import Source, SourceClass

    bad_source = Source(
        url="https://example.org/dead",
        **{"class": SourceClass.GOVERNMENT},
        fetched_at=datetime.now(UTC),
        status=503,
        content_hash="d",
    )
    with pytest.raises(BrokenSourceError):
        preflight(make_card(citations=[bad_source]))


def test_good_card_passes() -> None:
    preflight(make_card())
