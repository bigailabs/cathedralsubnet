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
        """V1 launch baseline (CONTRACTS.md Section 9 lock #12).

        These IDs supersede the earlier baseline. The full per-card
        definition (description, eval_spec_md, source_pool,
        task_templates, scoring_rubric) lives in the
        `card_definitions` DB table populated from the
        `cathedral-eval-spec` content repo. The in-process registry
        kept here is the lightweight scoring-side view used by the
        existing `score_card`/`preflight` code paths.
        """
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
            )
        )

    def lookup(self, card_id: str) -> RegistryEntry | None:
        return next((e for e in self.entries if e.card_id == card_id), None)
