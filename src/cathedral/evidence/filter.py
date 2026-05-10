"""Drop usage that should not count toward rewards (issue #2).

Excluded: creator-owned, platform-owned, refunded, abuse-flagged, test, and
self-loop. Self-loop is detected when consumer wallet matches owner wallet.
"""

from __future__ import annotations

from cathedral.types import PolarisUsageRecord


def filter_usage(records: list[PolarisUsageRecord], owner_wallet: str) -> list[PolarisUsageRecord]:
    return [
        u
        for u in records
        if not u.flagged
        and not u.refunded
        and u.consumer.counts_for_rewards()
        and not _is_self_loop(u, owner_wallet)
    ]


def _is_self_loop(u: PolarisUsageRecord, owner_wallet: str) -> bool:
    return u.consumer_wallet is not None and u.consumer_wallet == owner_wallet
