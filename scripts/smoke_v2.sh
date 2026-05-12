#!/usr/bin/env bash
# Cathedral v2 end-to-end smoke against a live Polaris instance.
#
# Usage:
#   POLARIS_BASE_URL=https://api.polaris.computer \
#   POLARIS_API_TOKEN=pi_sk_... \
#   POLARIS_ATTESTATION_PUBLIC_KEY=<64-char hex> \
#   CATHEDRAL_BUNDLE_KEK=<64-char hex> \
#   CATHEDRAL_BUNDLE_URL=https://r2.../bundle.bin?sig=... \
#   CATHEDRAL_BUNDLE_KEY_ID=kms-local:<b64>:<b64> \
#   CATHEDRAL_MINER_HOTKEY=5GrwvaEF... \
#   CATHEDRAL_CARD_ID=eu-ai-act \
#   bash scripts/smoke_v2.sh
#
# Drives the full v2 path: POST /api/cathedral/v1/deploy -> POST /chat ->
# GET manifest. Prints the captured Card + trace shape on success. Exits
# non-zero on any failure.
#
# Reuses the prod runner module so the smoke exercises the SAME code
# path the validator would in production. The only mock is the EvalTask
# (we generate a deterministic one inline instead of pulling from the
# card registry).

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [ ! -d .venv ]; then
  echo "[smoke-v2] set up .venv first: python3.11 -m venv .venv && .venv/bin/pip install -e .[dev]"
  exit 1
fi

: "${POLARIS_BASE_URL:?POLARIS_BASE_URL is required}"
: "${POLARIS_API_TOKEN:?POLARIS_API_TOKEN is required}"
: "${POLARIS_ATTESTATION_PUBLIC_KEY:?POLARIS_ATTESTATION_PUBLIC_KEY is required}"
: "${CATHEDRAL_BUNDLE_KEK:?CATHEDRAL_BUNDLE_KEK is required}"
: "${CATHEDRAL_BUNDLE_URL:?CATHEDRAL_BUNDLE_URL is required}"
: "${CATHEDRAL_BUNDLE_KEY_ID:?CATHEDRAL_BUNDLE_KEY_ID is required}"
: "${CATHEDRAL_MINER_HOTKEY:?CATHEDRAL_MINER_HOTKEY is required}"
: "${CATHEDRAL_CARD_ID:=eu-ai-act}"

PY=".venv/bin/python"

PYTHONPATH="$ROOT/src:$ROOT" $PY - <<'PYEOF'
"""Cathedral v2 smoke runner.

Imports `polaris_deploy_runner` directly (bypasses the publisher app
bootstrap) so this script doesn't need a Hippius client, validator
DB, or any orchestrator state. We hand-roll a `FixedUrlResolver` that
returns the pre-staged bundle URL.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent if "__file__" in dir() else Path.cwd()

# Load polaris_runner FIRST so cathedral.eval.polaris_runner resolves
# to the same module across both legacy and v2 imports.
pr_spec = importlib.util.spec_from_file_location(
    "cathedral.eval.polaris_runner",
    ROOT / "src/cathedral/eval/polaris_runner.py",
)
pr = importlib.util.module_from_spec(pr_spec)
sys.modules["cathedral.eval.polaris_runner"] = pr
pr_spec.loader.exec_module(pr)

pdr_spec = importlib.util.spec_from_file_location(
    "cathedral.eval.polaris_deploy_runner",
    ROOT / "src/cathedral/eval/polaris_deploy_runner.py",
)
pdr = importlib.util.module_from_spec(pdr_spec)
sys.modules["cathedral.eval.polaris_deploy_runner"] = pdr
pdr_spec.loader.exec_module(pdr)

from cathedral.v1_types import EvalTask  # noqa: E402


class FixedUrlResolver:
    """Resolver that returns the env-supplied bundle URL verbatim."""

    def __init__(self, url: str) -> None:
        self.url = url

    def url_for(self, submission):
        return self.url


async def main() -> int:
    cfg = pdr.PolarisDeployRunnerConfig(
        base_url=os.environ["POLARIS_BASE_URL"].rstrip("/"),
        api_token=os.environ["POLARIS_API_TOKEN"],
        bundle_url_resolver=FixedUrlResolver(os.environ["CATHEDRAL_BUNDLE_URL"]),
        attestation_public_key_hex=os.environ["POLARIS_ATTESTATION_PUBLIC_KEY"],
        bundle_encryption_key_hex=os.environ["CATHEDRAL_BUNDLE_KEK"],
        ttl_minutes=int(os.environ.get("POLARIS_DEPLOY_TTL_MINUTES", "30")),
    )
    runner = pdr.PolarisDeployRunner(cfg)
    card_id = os.environ.get("CATHEDRAL_CARD_ID", "eu-ai-act")
    task = EvalTask(
        card_id=card_id,
        epoch=0,
        round_index=0,
        prompt=f"Produce the regulatory card for {card_id}.",
        sources=[],
        deadline_minutes=25,
    )
    submission = {
        "id": "smoke-v2-001",
        "encryption_key_id": os.environ["CATHEDRAL_BUNDLE_KEY_ID"],
        "card_id": card_id,
        "epoch": 0,
        "round_index": 0,
    }
    print("[smoke-v2] dispatching to", cfg.base_url)
    result = await runner.run(
        bundle_bytes=b"",
        bundle_hash="0" * 64,
        task=task,
        miner_hotkey=os.environ["CATHEDRAL_MINER_HOTKEY"],
        submission=submission,
    )
    print("[smoke-v2] OK")
    print("  polaris_agent_id:", result.polaris_agent_id)
    print("  output_card jurisdiction:", result.output_card_json.get("jurisdiction"))
    print("  trace tool_calls:", len(result.trace.get("tool_calls", [])))
    print("  trace model_calls:", len(result.trace.get("model_calls", [])))
    print("  manifest owner_wallet:", result.manifest.get("owner_wallet"))
    return 0


sys.exit(asyncio.run(main()))
PYEOF
