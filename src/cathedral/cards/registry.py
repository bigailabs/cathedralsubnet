"""Card registry — what Cathedral expects to see, by topic.

Issue #3 first baseline (per the cathedral repo): a small set of
jurisdictions and topics where official source quality is high.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from cathedral.types import Jurisdiction, SourceClass


@dataclass(frozen=True)
class RegistryEntry:
    card_id: str
    jurisdiction: Jurisdiction
    topic: str
    required_source_classes: tuple[SourceClass, ...]
    refresh_cadence_hours: int


@dataclass(frozen=True)
class CardRegistry:
    entries: tuple[RegistryEntry, ...] = field(default_factory=tuple)

    @classmethod
    def baseline(cls) -> CardRegistry:
        return cls(
            entries=(
                RegistryEntry(
                    card_id="eu-ai-act",
                    jurisdiction=Jurisdiction.EU,
                    topic="EU AI Act enforcement and guidance",
                    required_source_classes=(
                        SourceClass.OFFICIAL_JOURNAL,
                        SourceClass.REGULATOR,
                        SourceClass.LAW_TEXT,
                    ),
                    refresh_cadence_hours=24,
                ),
                RegistryEntry(
                    card_id="us-ai-executive-order",
                    jurisdiction=Jurisdiction.US,
                    topic="US executive orders and federal AI guidance",
                    required_source_classes=(SourceClass.GOVERNMENT, SourceClass.REGULATOR),
                    refresh_cadence_hours=24,
                ),
                RegistryEntry(
                    card_id="uk-aisi",
                    jurisdiction=Jurisdiction.UK,
                    topic="UK AI Safety Institute publications",
                    required_source_classes=(SourceClass.GOVERNMENT, SourceClass.REGULATOR),
                    refresh_cadence_hours=48,
                ),
                RegistryEntry(
                    card_id="eu-gdpr-enforcement",
                    jurisdiction=Jurisdiction.EU,
                    topic="GDPR enforcement decisions and fines",
                    required_source_classes=(SourceClass.REGULATOR, SourceClass.COURT),
                    refresh_cadence_hours=24,
                ),
                RegistryEntry(
                    card_id="us-ccpa",
                    jurisdiction=Jurisdiction.US,
                    topic="California Consumer Privacy Act enforcement",
                    required_source_classes=(
                        SourceClass.REGULATOR,
                        SourceClass.LAW_TEXT,
                        SourceClass.COURT,
                    ),
                    refresh_cadence_hours=48,
                ),
            )
        )

    def lookup(self, card_id: str) -> RegistryEntry | None:
        return next((e for e in self.entries if e.card_id == card_id), None)
