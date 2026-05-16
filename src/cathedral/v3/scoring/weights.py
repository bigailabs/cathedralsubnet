"""EMA-based weight setter.

Per-miner weight = EMA(score) over their recent trajectories, normalized
across miners. The EMA half-life is configurable via env. The weight loop
optionally pushes to the bittensor subtensor when gated on by env.
"""

from __future__ import annotations

import math
import os
from collections import defaultdict
from datetime import UTC, datetime

from cathedral.v3.archive import TrajectoryArchive
from cathedral.v3.types import Weights

_DEFAULT_HALF_LIFE = int(os.environ.get("CATHEDRAL_V3_EMA_HALF_LIFE", "50"))


def compute_weights(archive: TrajectoryArchive, half_life: int = _DEFAULT_HALF_LIFE) -> Weights:
    """EMA over (chronological) trajectories, per miner, normalized."""
    decay = math.log(2) / max(1, half_life)

    sums: dict[str, float] = defaultdict(float)
    norms: dict[str, float] = defaultdict(float)
    counts: dict[str, int] = defaultdict(int)
    last_t: dict[str, int] = {}
    t = 0
    for traj in archive.iter_all():
        t += 1
        hk = traj.miner_hotkey
        # decay each miner's accumulator independently using its own clock
        prev = last_t.get(hk, t)
        decay_factor = math.exp(-decay * (t - prev))
        sums[hk] = sums[hk] * decay_factor + traj.score.weighted
        norms[hk] = norms[hk] * decay_factor + 1.0
        last_t[hk] = t
        counts[hk] += 1

    per_miner_raw = {hk: (sums[hk] / norms[hk]) if norms[hk] > 0 else 0.0 for hk in sums}
    total = sum(per_miner_raw.values()) or 1.0
    per_miner_norm = {hk: v / total for hk, v in per_miner_raw.items()}
    return Weights(
        per_miner=per_miner_norm,
        trajectory_count=dict(counts),
        half_life=half_life,
        on_chain=False,
        computed_at=datetime.now(UTC),
    )


class WeightLoop:
    """Wraps compute_weights with optional chain emission."""

    def __init__(self, archive: TrajectoryArchive, half_life: int = _DEFAULT_HALF_LIFE) -> None:
        self.archive = archive
        self.half_life = half_life
        self.chain_enabled = os.environ.get("CATHEDRAL_V3_CHAIN_ENABLED") == "1"

    def step(self) -> Weights:
        w = compute_weights(self.archive, self.half_life)
        if self.chain_enabled:
            try:
                self._push_to_chain(w)
                w.on_chain = True
            except Exception:
                # do not propagate — weight setting must not crash the loop
                w.on_chain = False
        return w

    def _push_to_chain(self, w: Weights) -> None:  # pragma: no cover - infra path
        try:
            import bittensor
        except ImportError:
            return
        wallet_name = os.environ.get("CATHEDRAL_V3_WALLET", "default")
        netuid = int(os.environ.get("CATHEDRAL_V3_NETUID", "39"))
        network = os.environ.get("CATHEDRAL_V3_NETWORK", "finney")
        sub = bittensor.Subtensor(network=network)
        meta = sub.metagraph(netuid=netuid)
        wallet = bittensor.Wallet(name=wallet_name)
        uids: list[int] = []
        vals: list[float] = []
        for hk, weight in w.per_miner.items():
            if hk in meta.hotkeys:
                uids.append(meta.hotkeys.index(hk))
                vals.append(weight)
        if not uids:
            return
        sub.set_weights(wallet=wallet, netuid=netuid, uids=uids, weights=vals)


__all__ = ["WeightLoop", "compute_weights"]
