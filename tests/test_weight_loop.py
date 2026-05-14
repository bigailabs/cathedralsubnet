from datetime import UTC, datetime

import pytest

from cathedral.chain.client import Metagraph, MinerNode, WeightStatus
from cathedral.chain.mock import MockChain
from cathedral.validator import weight_loop
from cathedral.validator.db import connect
from cathedral.validator.health import Health
from cathedral.validator.pull_loop import upsert_pulled_eval


@pytest.mark.asyncio
async def test_disabled_weight_loop_computes_without_submitting(tmp_path, monkeypatch) -> None:
    conn = await connect(str(tmp_path / "validator.db"))
    now = datetime.now(UTC).isoformat()
    await upsert_pulled_eval(
        conn,
        eval_run={"id": "run-1", "weighted_score": 0.75, "ran_at": now},
        miner_hotkey="hotkey-1",
    )

    chain = MockChain(
        Metagraph(
            block=7,
            miners=(
                MinerNode(uid=0, hotkey="burn-hotkey", last_update_block=1),
                MinerNode(uid=42, hotkey="hotkey-1", last_update_block=1),
            ),
        )
    )
    health = Health()
    info_events: list[tuple[str, dict]] = []

    class FakeLogger:
        def info(self, event: str, **fields: object) -> None:
            info_events.append((event, fields))

        def warning(self, event: str, **fields: object) -> None:
            pass

        def debug(self, event: str, **fields: object) -> None:
            pass

    monkeypatch.setattr(weight_loop, "logger", FakeLogger())

    stop = weight_loop.asyncio.Event()
    task = weight_loop.asyncio.create_task(
        weight_loop.run_weight_loop(
            conn,
            chain,
            health,
            interval_secs=60,
            disabled=True,
            burn_uid=0,
            forced_burn_percentage=98.0,
            stop=stop,
        )
    )
    try:
        for _ in range(50):
            snapshot = await health.get()
            if snapshot.last_weight_set_at is not None:
                break
            await weight_loop.asyncio.sleep(0.02)
        else:
            raise AssertionError("disabled weight loop did not complete one dry-run tick")

        snapshot = await health.get()
        assert snapshot.weight_status is WeightStatus.DISABLED
        assert snapshot.current_block == 7
        assert chain.last_weights == []
        assert ("weights_pre_burn", {
            "total_hotkeys": 1,
            "mapped_hotkeys": 1,
            "positive_hotkeys": 1,
            "unmapped_count": 0,
            "unmapped_sample": [],
            "positive_sample": [(42, 0.75)],
        }) in info_events
        assert any(
            event == "weights_set"
            and fields["status"] == WeightStatus.DISABLED.value
            and fields["uids"] == [42, 0]
            for event, fields in info_events
        )
    finally:
        stop.set()
        await weight_loop.asyncio.wait_for(task, timeout=1)
        await conn.close()
