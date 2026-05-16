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


# --------------------------------------------------------------------------
# HTTP-level archived-card 404 behavior
# --------------------------------------------------------------------------


async def _seed_archived_us_ai_eo_via_ctx(ctx: Any) -> None:
    """Seed an archived us-ai-eo row using the publisher app's own
    aiosqlite connection (`ctx.db`), so we hit the same DB the live
    endpoint reads from."""
    await repository.insert_card_definition(
        ctx.db,
        id="us-ai-eo",
        display_name="US AI EO (deprecated)",
        jurisdiction="us",
        topic="deprecated",
        description="Deprecated launch-plan card.",
        eval_spec_md="deprecated",
        source_pool=[],
        task_templates=[],
        scoring_rubric={},
        refresh_cadence_hours=24,
        status="archived",
    )
    await ctx.db.commit()


def test_eval_spec_endpoint_returns_404_for_archived_card(publisher_client) -> None:
    """``GET /v1/cards/{id}/eval-spec`` must return 404 for archived
    cards, mirroring the submit gate at ``publisher/submit.py``.

    Without this, archived launch-plan cards keep advertising their
    eval-spec content via the public endpoint even though new submits
    return 404, which would lead miners to build against cards they
    cannot actually submit to.
    """
    ctx = publisher_client.app.state.ctx
    asyncio.run(_seed_archived_us_ai_eo_via_ctx(ctx))

    resp = publisher_client.get("/v1/cards/us-ai-eo/eval-spec")
    assert resp.status_code == 404, (
        f"archived card must 404 from eval-spec, got {resp.status_code}: "
        f"{resp.text}"
    )
    assert "card not active" in resp.text or "card not found" in resp.text

    # Sanity check: the active eu-ai-act card still returns 200 from
    # the same endpoint.
    resp_ok = publisher_client.get("/v1/cards/eu-ai-act/eval-spec")
    assert resp_ok.status_code == 200, (
        f"active eu-ai-act must still serve eval-spec, got "
        f"{resp_ok.status_code}: {resp_ok.text}"
    )


def test_eval_spec_endpoint_returns_404_for_unknown_card(publisher_client) -> None:
    """Sanity guard: never-seeded card_ids still 404 (the archived-card
    gate is additive, not a regression of the existing
    'card not found' path)."""
    resp = publisher_client.get("/v1/cards/never-seeded-ever/eval-spec")
    assert resp.status_code == 404, resp.text


# --------------------------------------------------------------------------
# Archived cards must 404 across every /v1/cards/{card_id}/* surface
# --------------------------------------------------------------------------


# Every /v1/cards/{card_id}/* route that surfaces card content. If a new
# route lands and forgets to call get_active_card_definition_or_404, the
# parametrised test below will fail on it once it's added here.
_PUBLIC_CARD_SUBPATHS: list[str] = [
    "",  # GET /v1/cards/{card_id} (summary)
    "/eval-spec",
    "/history",
    "/feed",
    "/attempts",
    "/discovery",
    "/discovery/count",
]


@pytest.mark.parametrize("subpath", _PUBLIC_CARD_SUBPATHS)
def test_archived_card_404s_across_all_public_subpaths(
    publisher_client, subpath: str
) -> None:
    """Every /v1/cards/{card_id}/* surface must 404 on archived cards.

    Without the shared `get_active_card_definition_or_404` helper, each
    route was independently checking only existence (not status), so an
    archived row would still serve summary/history/feed/discovery
    content even though submit and eval-spec correctly rejected it.
    """
    ctx = publisher_client.app.state.ctx
    asyncio.run(_seed_archived_us_ai_eo_via_ctx(ctx))

    resp = publisher_client.get(f"/v1/cards/us-ai-eo{subpath}")
    assert resp.status_code == 404, (
        f"GET /v1/cards/us-ai-eo{subpath} must return 404 for archived "
        f"card, got {resp.status_code}: {resp.text}"
    )


def test_leaderboard_404s_for_archived_card(publisher_client) -> None:
    """`GET /v1/leaderboard?card=<archived>` must 404 too: archived
    cards should not appear anywhere a miner or the site might look."""
    ctx = publisher_client.app.state.ctx
    asyncio.run(_seed_archived_us_ai_eo_via_ctx(ctx))

    resp = publisher_client.get("/v1/leaderboard", params={"card": "us-ai-eo"})
    assert resp.status_code == 404, resp.text
