"""Regression tests pinning the v1 launch surface to EU AI Act only.

These guard against accidental re-introduction of the deprecated 5-card
launch plan (`us-ai-eo`, `uk-ai-whitepaper`, `singapore-pdpc`,
`japan-meti-mic`) in any code path that constitutes "the launch set":

- `CardRegistry.baseline()`
- `_V1_LAUNCH_CARDS` in publisher.app
- `_V1_DEPRECATED_CARD_IDS` in publisher.app
- `archive-cards` CLI command behavior (submit / eval-spec return 404
  for archived rows)

If any of these fail, do not silently "fix the test"; check whether the
launch surface really changed and update the issue tracker first.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from cathedral.cards.registry import CardRegistry
from cathedral.publisher import repository
from cathedral.publisher.app import _V1_DEPRECATED_CARD_IDS, _V1_LAUNCH_CARDS
from cathedral.validator.db import connect as connect_db


DEPRECATED = ("us-ai-eo", "uk-ai-whitepaper", "singapore-pdpc", "japan-meti-mic")


def test_registry_baseline_is_eu_ai_act_only() -> None:
    baseline = CardRegistry.baseline()
    ids = tuple(e.card_id for e in baseline.entries)
    assert ids == ("eu-ai-act",), (
        f"v1 launch is EU AI Act only; baseline registry must contain "
        f"exactly that one entry, got {ids}"
    )


def test_v1_launch_cards_is_eu_ai_act_only() -> None:
    ids = tuple(c["id"] for c in _V1_LAUNCH_CARDS)
    assert ids == ("eu-ai-act",), (
        f"_V1_LAUNCH_CARDS drives seed-cards on container start. v1 "
        f"launch is EU AI Act only; got {ids}"
    )


def test_deprecated_card_ids_match_archival_list() -> None:
    assert set(_V1_DEPRECATED_CARD_IDS) == set(DEPRECATED), (
        f"_V1_DEPRECATED_CARD_IDS must match the launch-PR's archival "
        f"list; got {_V1_DEPRECATED_CARD_IDS}"
    )


def test_archive_cards_marks_row_archived_idempotent(tmp_path: Path) -> None:
    """archive-cards: existing active row flips to archived; calling
    again is a no-op; missing row is silently skipped."""

    db_path = tmp_path / "publisher.db"

    async def _run() -> None:
        conn = await connect_db(str(db_path))
        try:
            # Seed an active deprecated row (simulating a fresh DB that
            # was previously seeded with the 5-card plan).
            await repository.insert_card_definition(
                conn,
                id="us-ai-eo",
                display_name="US AI EO (deprecated)",
                jurisdiction="us",
                topic="deprecated",
                description="x",
                eval_spec_md="x",
                source_pool=[],
                task_templates=[],
                scoring_rubric={},
                refresh_cadence_hours=24,
                status="active",
            )
            await conn.commit()

            # First archive flips it.
            updated = await repository.set_card_definition_status(
                conn, card_id="us-ai-eo", status="archived"
            )
            await conn.commit()
            assert updated is True

            row = await repository.get_card_definition(conn, "us-ai-eo")
            assert row is not None
            assert row["status"] == "archived"

            # Second archive is a no-op (already archived).
            updated2 = await repository.set_card_definition_status(
                conn, card_id="us-ai-eo", status="archived"
            )
            await conn.commit()
            assert updated2 is True  # row exists; UPDATE matched

            # Missing card: returns False, no insert.
            updated3 = await repository.set_card_definition_status(
                conn, card_id="never-seeded", status="archived"
            )
            await conn.commit()
            assert updated3 is False
            assert await repository.get_card_definition(conn, "never-seeded") is None
        finally:
            await conn.close()

    asyncio.run(_run())


def test_archived_card_status_routes_through_submit_check(tmp_path: Path) -> None:
    """The submit pipeline's card-status gate (``card_def['status'] !=
    'active'``) is the production trust posture for archived cards.

    Asserts the exact contract the gate depends on: archived rows still
    return from ``get_card_definition`` (so the gate can see them) but
    with ``status='archived'``, which triggers the HTTP 404 raise at
    ``publisher/submit.py``.
    """

    db_path = tmp_path / "publisher.db"

    async def _run() -> None:
        conn = await connect_db(str(db_path))
        try:
            await repository.insert_card_definition(
                conn,
                id="us-ai-eo",
                display_name="US AI EO (deprecated)",
                jurisdiction="us",
                topic="deprecated",
                description="x",
                eval_spec_md="x",
                source_pool=[],
                task_templates=[],
                scoring_rubric={},
                refresh_cadence_hours=24,
                status="archived",
            )
            await conn.commit()

            row = await repository.get_card_definition(conn, "us-ai-eo")
            assert row is not None, (
                "archived rows must remain readable so the submit gate "
                "can see them and return 404 (not silently 'card not found')"
            )
            assert row["status"] != "active", (
                f"archived card must not have status=active; got "
                f"{row['status']!r}. The submit gate compares != 'active'."
            )
        finally:
            await conn.close()

    asyncio.run(_run())
