"""Smoke-test launcher: builds the validator app with a MockChain so the
HTTP path, worker, and weight loop run end-to-end without a real Subtensor.

Usage:
    python -m scripts.smoke_launcher --config /path/to/validator.toml
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import uvicorn
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from cathedral.cards.registry import CardRegistry
from cathedral.chain.client import Metagraph, MinerNode
from cathedral.chain.mock import MockChain
from cathedral.config import ValidatorSettings
from cathedral.evidence import EvidenceCollector, HttpPolarisFetcher
from cathedral.logging import configure
from cathedral.validator import build_app
from cathedral.validator.config_runtime import RuntimeContext
from cathedral.validator.health import Health


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()

    configure(json_logs=False, level="INFO")
    settings = ValidatorSettings.from_toml(args.config)
    bearer = os.environ.get(settings.http.bearer_token_env, "dev-token")

    pubkey = Ed25519PublicKey.from_public_bytes(bytes.fromhex(settings.polaris.public_key_hex))
    fetcher = HttpPolarisFetcher(settings.polaris.base_url, settings.polaris.fetch_timeout_secs)
    collector = EvidenceCollector(fetcher, pubkey)

    chain = MockChain(
        Metagraph(
            block=1,
            miners=(MinerNode(uid=0, hotkey="5Miner", last_update_block=1),),
        )
    )

    Path(settings.storage.database_path).parent.mkdir(parents=True, exist_ok=True)

    ctx = RuntimeContext(
        settings=settings,
        bearer=bearer,
        chain=chain,
        collector=collector,
        registry=CardRegistry.baseline(),
        health=Health(),
        fetcher_close=fetcher.aclose,
    )
    app = build_app(ctx)

    uvicorn.run(
        app,
        host=settings.http.listen_host,
        port=settings.http.listen_port,
        log_level="warning",
    )


if __name__ == "__main__":
    main()
