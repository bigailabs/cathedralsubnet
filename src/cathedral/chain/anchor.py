"""On-chain Merkle root anchor via `system.remarkWithEvent`.

CONTRACTS.md Section 4.6: The Merkle root for an epoch is committed
on-chain via `system.remarkWithEvent` carrying the bytes:

    b"cath:v1:" + epoch.to_bytes(4, "big") + bytes.fromhex(merkle_root)

Total: 8 + 4 + 32 = 44 bytes. Validators index `system.remarkWithEvent`
events from the cathedral validator hotkey to recover the anchor.

We expose an `Anchorer` Protocol so tests can substitute a fake without
spinning up a real Substrate connection.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Protocol

_PREFIX = b"cath:v1:"
_EPOCH_BYTES = 4
_ROOT_BYTES = 32


class AnchorError(Exception):
    """On-chain extrinsic failed. Caller may retry."""


@dataclass(frozen=True)
class AnchorResult:
    block: int
    extrinsic_index: int


def encode_anchor_payload(epoch: int, merkle_root_hex: str) -> bytes:
    """Build the 44-byte payload that goes into the remark."""
    if epoch < 0 or epoch >= 2**32:
        raise AnchorError(f"epoch {epoch} out of range for u32")
    try:
        root = bytes.fromhex(merkle_root_hex)
    except ValueError as e:
        raise AnchorError(f"merkle_root not hex: {e}") from e
    if len(root) != _ROOT_BYTES:
        raise AnchorError(
            f"merkle_root must be {_ROOT_BYTES} bytes, got {len(root)}"
        )
    return _PREFIX + epoch.to_bytes(_EPOCH_BYTES, "big") + root


# Public alias matching CONTRACTS.md §4.6 reference name. Tests import
# `cathedral.chain.anchor.encode_commit` directly.
encode_commit = encode_anchor_payload
build_commit_payload = encode_anchor_payload


class Anchorer(Protocol):
    """Submits Merkle root anchors to the chain. Test-substitutable."""

    async def anchor(self, epoch: int, merkle_root_hex: str) -> AnchorResult: ...


class BittensorAnchorer:
    """Production anchorer using `bittensor` SDK + substrate interface.

    The bittensor SDK doesn't expose `system.remarkWithEvent` directly, so
    we drop down to the underlying `subtensor.substrate` SubstrateInterface
    and compose the call manually. Same wallet hotkey signs the extrinsic.
    """

    def __init__(
        self,
        *,
        network: str,
        wallet_name: str,
        wallet_hotkey: str,
        wallet_path: str | None = None,
    ) -> None:
        self.network = network
        self.wallet_name = wallet_name
        self.wallet_hotkey = wallet_hotkey
        self.wallet_path = wallet_path
        self._subtensor: Any = None
        self._wallet: Any = None

    def _ensure_clients(self) -> None:
        if self._subtensor is not None:
            return
        import bittensor as bt

        wallet_kwargs: dict[str, Any] = {
            "name": self.wallet_name,
            "hotkey": self.wallet_hotkey,
        }
        if self.wallet_path:
            wallet_kwargs["path"] = self.wallet_path
        self._wallet = bt.Wallet(**wallet_kwargs)
        self._subtensor = bt.Subtensor(network=self.network)

    async def anchor(self, epoch: int, merkle_root_hex: str) -> AnchorResult:
        payload = encode_anchor_payload(epoch, merkle_root_hex)

        def _send() -> AnchorResult:
            self._ensure_clients()
            substrate = getattr(self._subtensor, "substrate", None)
            if substrate is None:
                raise AnchorError("subtensor has no substrate interface attached")
            try:
                call = substrate.compose_call(
                    call_module="System",
                    call_function="remark_with_event",
                    call_params={"remark": payload},
                )
                extrinsic = substrate.create_signed_extrinsic(
                    call=call, keypair=self._wallet.hotkey
                )
                receipt = substrate.submit_extrinsic(
                    extrinsic, wait_for_inclusion=True, wait_for_finalization=False
                )
            except Exception as e:
                raise AnchorError(f"extrinsic submit failed: {e}") from e

            block = int(getattr(receipt, "block_number", 0) or 0)
            ext_idx = int(getattr(receipt, "extrinsic_idx", 0) or 0)
            if not getattr(receipt, "is_success", True):
                raise AnchorError(
                    f"extrinsic not successful: {getattr(receipt, 'error_message', '?')}"
                )
            return AnchorResult(block=block, extrinsic_index=ext_idx)

        return await asyncio.to_thread(_send)


class StubAnchorer:
    """Records calls in-memory; for unit + integration tests."""

    def __init__(self) -> None:
        self.calls: list[tuple[int, str]] = []
        self._block = 0

    async def anchor(self, epoch: int, merkle_root_hex: str) -> AnchorResult:
        # Validates payload encoding the same way production does.
        encode_anchor_payload(epoch, merkle_root_hex)
        self.calls.append((epoch, merkle_root_hex))
        self._block += 1
        return AnchorResult(block=self._block, extrinsic_index=0)
