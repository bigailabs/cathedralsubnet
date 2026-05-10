"""Bittensor chain integration.

Wraps the official `bittensor` SDK. Blocking SDK calls run inside
`asyncio.to_thread` so the validator's async loop is not stalled.

The `Chain` Protocol lets tests substitute `MockChain` without touching
real chain RPC.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol


class WeightStatus(str, Enum):
    HEALTHY = "healthy"
    BLOCKED_BY_STAKE = "blocked_by_stake"
    BLOCKED_BY_TRANSACTION_ERROR = "blocked_by_transaction_error"
    DISABLED = "disabled"


@dataclass(frozen=True)
class MinerNode:
    uid: int
    hotkey: str
    last_update_block: int


@dataclass(frozen=True)
class Metagraph:
    block: int
    miners: tuple[MinerNode, ...]

    def hotkey_to_uid(self) -> dict[str, int]:
        return {m.hotkey: m.uid for m in self.miners}


class Chain(Protocol):
    async def metagraph(self) -> Metagraph: ...
    async def set_weights(self, weights: list[tuple[int, float]]) -> WeightStatus: ...
    async def current_block(self) -> int: ...


def network_endpoint(name: str) -> str:
    if name == "finney":
        return "wss://entrypoint-finney.opentensor.ai:443"
    if name == "test":
        return "wss://test.finney.opentensor.ai:443"
    if name == "local":
        return "ws://127.0.0.1:9944"
    raise ValueError(f"unknown network {name!r}")


class BittensorChain:
    """Production chain client backed by the bittensor SDK.

    Imports are lazy so tests that don't need bittensor (the heavy install)
    can run on environments where the package isn't available.
    """

    def __init__(
        self,
        network: str,
        netuid: int,
        wallet_name: str,
        wallet_hotkey: str,
        wallet_path: str | None = None,
    ) -> None:
        self.network = network
        self.netuid = netuid
        self.wallet_name = wallet_name
        self.wallet_hotkey = wallet_hotkey
        self.wallet_path = wallet_path
        self._subtensor: Any = None
        self._wallet: Any = None

    def _ensure_clients(self) -> None:
        if self._subtensor is None:
            import bittensor as bt  # local import; heavy

            kwargs: dict[str, Any] = {"name": self.wallet_name, "hotkey": self.wallet_hotkey}
            if self.wallet_path:
                kwargs["path"] = self.wallet_path
            self._wallet = bt.wallet(**kwargs)
            self._subtensor = bt.subtensor(network=self.network)

    async def metagraph(self) -> Metagraph:
        def _read() -> Metagraph:
            self._ensure_clients()
            mg = self._subtensor.metagraph(netuid=self.netuid)
            miners = tuple(
                MinerNode(
                    uid=int(uid),
                    hotkey=str(hk),
                    last_update_block=int(mg.last_update[i]) if hasattr(mg, "last_update") else 0,
                )
                for i, (uid, hk) in enumerate(zip(mg.uids.tolist(), mg.hotkeys, strict=False))
            )
            return Metagraph(block=int(mg.block.item()), miners=miners)

        return await asyncio.to_thread(_read)

    async def set_weights(self, weights: list[tuple[int, float]]) -> WeightStatus:
        if not weights:
            return WeightStatus.HEALTHY  # nothing to send

        def _send() -> WeightStatus:
            self._ensure_clients()
            uids = [u for u, _ in weights]
            values = [v for _, v in weights]
            try:
                ok, _msg = self._subtensor.set_weights(
                    wallet=self._wallet,
                    netuid=self.netuid,
                    uids=uids,
                    weights=values,
                    wait_for_inclusion=False,
                )
                return WeightStatus.HEALTHY if ok else WeightStatus.BLOCKED_BY_TRANSACTION_ERROR
            except Exception as e:
                msg = str(e).lower()
                if "stake" in msg or "permit" in msg:
                    return WeightStatus.BLOCKED_BY_STAKE
                return WeightStatus.BLOCKED_BY_TRANSACTION_ERROR

        return await asyncio.to_thread(_send)

    async def current_block(self) -> int:
        def _read() -> int:
            self._ensure_clients()
            return int(self._subtensor.get_current_block())

        return await asyncio.to_thread(_read)
