"""Bittensor chain integration.

Wraps the official `bittensor` SDK (v10.x). Blocking SDK calls run inside
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
    async def is_registered(self) -> bool: ...


def network_endpoint(name: str) -> str:
    if name == "finney":
        return "wss://entrypoint-finney.opentensor.ai:443"
    if name == "test":
        return "wss://test.finney.opentensor.ai:443"
    if name == "local":
        return "ws://127.0.0.1:9944"
    raise ValueError(f"unknown network {name!r}")


# Substring fragments of bittensor error messages that indicate the validator
# is below the permit-stake threshold. The SDK does not expose a structured
# error code for this, so we match on text.
_STAKE_BLOCK_FRAGMENTS = (
    "stake",
    "permit",
    "min_allowed_weights",
    "validator permit",
)


class BittensorChain:
    """Production chain client backed by the bittensor SDK."""

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
        if self._subtensor is not None:
            return
        import bittensor as bt  # local import; heavy

        wallet_kwargs: dict[str, Any] = {
            "name": self.wallet_name,
            "hotkey": self.wallet_hotkey,
        }
        if self.wallet_path:
            wallet_kwargs["path"] = self.wallet_path
        self._wallet = bt.Wallet(**wallet_kwargs)
        self._subtensor = bt.Subtensor(network=self.network)

    async def metagraph(self) -> Metagraph:
        def _read() -> Metagraph:
            self._ensure_clients()
            mg = self._subtensor.metagraph(netuid=self.netuid, lite=True)
            uids = mg.uids.tolist()
            hotkeys = list(mg.hotkeys)
            last_update = mg.last_update.tolist() if hasattr(mg, "last_update") else [0] * len(uids)
            miners = tuple(
                MinerNode(
                    uid=int(uid),
                    hotkey=str(hk),
                    last_update_block=int(lu) if i < len(last_update) else 0,
                )
                for i, (uid, hk, lu) in enumerate(
                    zip(uids, hotkeys, last_update + [0] * len(uids), strict=False)
                )
            )
            block = int(mg.block.item()) if hasattr(mg.block, "item") else int(mg.block[0])
            return Metagraph(block=block, miners=miners)

        return await asyncio.to_thread(_read)

    async def is_registered(self) -> bool:
        def _check() -> bool:
            self._ensure_clients()
            return bool(
                self._subtensor.is_hotkey_registered_on_subnet(
                    hotkey_ss58=self._wallet.hotkey.ss58_address,
                    netuid=self.netuid,
                )
            )

        return await asyncio.to_thread(_check)

    async def set_weights(self, weights: list[tuple[int, float]]) -> WeightStatus:
        if not weights:
            return WeightStatus.HEALTHY  # nothing to send

        def _send() -> WeightStatus:
            self._ensure_clients()
            uids = [u for u, _ in weights]
            values = [v for _, v in weights]
            try:
                resp = self._subtensor.set_weights(
                    wallet=self._wallet,
                    netuid=self.netuid,
                    uids=uids,
                    weights=values,
                    wait_for_inclusion=True,
                    wait_for_finalization=False,
                    raise_error=False,
                )
            except Exception as e:
                return _classify_error(str(e))

            if getattr(resp, "success", False):
                return WeightStatus.HEALTHY
            return _classify_error(str(getattr(resp, "message", "")))

        return await asyncio.to_thread(_send)

    async def current_block(self) -> int:
        def _read() -> int:
            self._ensure_clients()
            return int(self._subtensor.get_current_block())

        return await asyncio.to_thread(_read)


def _classify_error(message: str) -> WeightStatus:
    lc = message.lower()
    for frag in _STAKE_BLOCK_FRAGMENTS:
        if frag in lc:
            return WeightStatus.BLOCKED_BY_STAKE
    return WeightStatus.BLOCKED_BY_TRANSACTION_ERROR
