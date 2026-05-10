"""Six-dimension card scorer (issue #3).

Each dimension returns a value in [0.0, 1.0]. Final weighting lives in
`ScoreParts.weighted` so the validator team can tune coefficients without
rewriting rules.
"""

from __future__ import annotations

from datetime import UTC, datetime

from cathedral.cards.registry import RegistryEntry
from cathedral.types import OFFICIAL_SOURCE_CLASSES, Card, ScoreParts


def score_card(card: Card, entry: RegistryEntry | None = None) -> ScoreParts:
    return ScoreParts(
        source_quality=_source_quality(card, entry),
        freshness=_freshness(card, entry),
        specificity=_specificity(card),
        usefulness=_usefulness(card),
        clarity=_clarity(card),
        maintenance=_maintenance(card, entry),
    )


def _source_quality(card: Card, entry: RegistryEntry | None) -> float:
    if not card.citations:
        return 0.0
    official = sum(1 for s in card.citations if s.class_ in OFFICIAL_SOURCE_CLASSES)
    base = official / len(card.citations)

    coverage_bonus = 0.0
    if entry and entry.required_source_classes:
        required = entry.required_source_classes
        covered = sum(1 for c in required if any(s.class_ == c for s in card.citations))
        coverage_bonus = 0.20 * (covered / len(required))

    return min(1.0, base + coverage_bonus)


def _freshness(card: Card, entry: RegistryEntry | None) -> float:
    age_hours = max(0.0, (datetime.now(UTC) - card.last_refreshed_at).total_seconds() / 3600)
    cadence = max(1, entry.refresh_cadence_hours if entry else card.refresh_cadence_hours)
    ratio = age_hours / cadence
    if ratio <= 1.0:
        return 1.0
    if ratio >= 4.0:
        return 0.0
    return 1.0 - (ratio - 1.0) / 3.0


def _specificity(card: Card) -> float:
    length = len(card.what_changed) + len(card.why_it_matters)
    if length < 100:
        return 0.2
    if length < 400:
        return 0.6
    if length < 1500:
        return 1.0
    return 0.7


def _usefulness(card: Card) -> float:
    score = 0.0
    if card.action_notes.strip():
        score += 0.5
    if card.risks.strip():
        score += 0.3
    if card.confidence > 0.5:
        score += 0.2
    return min(1.0, score)


def _clarity(card: Card) -> float:
    summary = card.summary.strip()
    if len(summary) < 40 or len(summary) > 800:
        return 0.4
    sentences = sum(1 for s in summary.split(".") if s.strip())
    return 1.0 if 1 <= sentences <= 6 else 0.6


def _maintenance(card: Card, entry: RegistryEntry | None) -> float:
    cadence = max(1, entry.refresh_cadence_hours if entry else card.refresh_cadence_hours)
    age_hours = (datetime.now(UTC) - card.last_refreshed_at).total_seconds() / 3600
    if age_hours <= cadence:
        return 1.0
    if age_hours <= cadence * 2:
        return 0.6
    if age_hours <= cadence * 4:
        return 0.2
    return 0.0
