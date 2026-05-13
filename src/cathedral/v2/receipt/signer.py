"""ed25519 receipt signer.

Default scheme is ed25519. A signing key is generated on first run and
persisted to `$CATHEDRAL_V2_HOME/signer.key`. Override with
`CATHEDRAL_V2_SIGNING_KEY` (hex seed) or `CATHEDRAL_V2_WALLET` (bittensor
wallet name; not implemented in this branch — falls back to ed25519).
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

from nacl.signing import SigningKey, VerifyKey

from cathedral.v2.types import Receipt, Trajectory


class ReceiptSigner:
    def __init__(self, signing_key: SigningKey) -> None:
        self._sk = signing_key
        self._vk: VerifyKey = signing_key.verify_key

    @property
    def public_hex(self) -> str:
        return bytes(self._vk).hex()

    def sign(self, traj: Trajectory) -> Receipt:
        if not traj.bundle_hash:
            traj.bundle_hash = traj.compute_bundle_hash()
        unsigned = Receipt(
            trajectory_id=traj.trajectory_id,
            job_id=traj.job.job_id,
            task_type=traj.job.task_type,
            miner_hotkey=traj.miner_hotkey,
            miner_kind=traj.miner_kind,
            score=traj.score.weighted,
            failure_class=traj.score.failure_class,
            readiness=traj.score.readiness,
            bundle_hash=traj.bundle_hash,
            signed_at=datetime.now(UTC),
            signature_scheme="ed25519",
            signer_pubkey_hex=self.public_hex,
            signature_hex="",
        )
        sig = self._sk.sign(unsigned.signing_payload()).signature.hex()
        return unsigned.model_copy(update={"signature_hex": sig})


def verify_receipt(receipt: Receipt) -> bool:
    if receipt.signature_scheme != "ed25519":
        return False
    try:
        vk = VerifyKey(bytes.fromhex(receipt.signer_pubkey_hex))
        vk.verify(receipt.signing_payload(), bytes.fromhex(receipt.signature_hex))
        return True
    except Exception:
        return False


def load_or_create_signing_key(home: Path) -> SigningKey:
    env_seed = os.environ.get("CATHEDRAL_V2_SIGNING_KEY")
    if env_seed:
        return SigningKey(bytes.fromhex(env_seed))
    home.mkdir(parents=True, exist_ok=True)
    key_path = home / "signer.key"
    if key_path.exists():
        return SigningKey(bytes.fromhex(key_path.read_text().strip()))
    sk = SigningKey.generate()
    key_path.write_text(bytes(sk).hex())
    try:
        os.chmod(key_path, 0o600)
    except OSError:
        pass
    return sk


__all__ = ["ReceiptSigner", "load_or_create_signing_key", "verify_receipt"]
