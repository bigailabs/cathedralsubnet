"""Preflight checks that fail fast before scoring.

Issue #3: broken sources, uncited claims, and legal-advice framing fail.
"""

from __future__ import annotations

from cathedral.types import Card

LEGAL_ADVICE_PHRASES: tuple[str, ...] = (
    "you should",
    "we recommend that you",
    "our advice is",
    "as your lawyer",
    "this constitutes legal advice",
)


class PreflightError(Exception):
    """Card failed preflight; should not be scored."""


class NoCitationsError(PreflightError):
    pass


class BrokenSourceError(PreflightError):
    def __init__(self, url: str, status: int) -> None:
        super().__init__(f"broken source {url} status {status}")
        self.url = url
        self.status = status


class LegalAdviceFramingError(PreflightError):
    def __init__(self, phrase: str) -> None:
        super().__init__(f"legal-advice framing detected: {phrase!r}")
        self.phrase = phrase


class MissingFieldError(PreflightError):
    def __init__(self, field: str) -> None:
        super().__init__(f"missing required field: {field}")
        self.field = field


class MissingNoLegalAdviceMarkerError(PreflightError):
    pass


def preflight(card: Card) -> None:
    """Raise on failure; return None on pass."""
    if not card.citations:
        raise NoCitationsError("card has no citations")
    if not card.no_legal_advice:
        raise MissingNoLegalAdviceMarkerError("card missing no_legal_advice marker")
    for required in ("summary", "what_changed", "why_it_matters"):
        if not getattr(card, required).strip():
            raise MissingFieldError(required)

    for src in card.citations:
        if not (200 <= src.status < 400):
            raise BrokenSourceError(src.url, src.status)

    blob = " ".join([card.summary, card.action_notes, card.why_it_matters]).lower()
    for phrase in LEGAL_ADVICE_PHRASES:
        if phrase in blob:
            raise LegalAdviceFramingError(phrase)
