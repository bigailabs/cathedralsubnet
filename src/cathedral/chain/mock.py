"""In-memory chain for tests and local development."""

from __future__ import annotations

from cathedral.chain.client import Metagraph, WeightStatus


class MockChain:
    def __init__(self, metagraph: Metagraph | None = None) -> None:
        self._metagraph = metagraph or Metagraph(block=0, miners=())
        self._block = self._metagraph.block
        self.last_weights: list[tuple[int, float]] = []
        self.weight_status_override: WeightStatus = WeightStatus.HEALTHY

    async def metagraph(self) -> Metagraph:
        return self._metagraph

    async def set_weights(self, weights: list[tuple[int, float]]) -> WeightStatus:
        self.last_weights = list(weights)
        return self.weight_status_override

    async def current_block(self) -> int:
        return self._block

    async def is_registered(self) -> bool:
        return True

    def set_metagraph(self, metagraph: Metagraph) -> None:
        self._metagraph = metagraph
        self._block = metagraph.block
