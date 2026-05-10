#!/usr/bin/env bash
# Run the validator locally against sqlite + a mock bearer.
# Useful for hacking on the loop without standing up Polaris/chain.
set -euo pipefail

mkdir -p data
export CATHEDRAL_BEARER="${CATHEDRAL_BEARER:-dev-token}"

if [ ! -d .venv ]; then
  python3.11 -m venv .venv
  .venv/bin/pip install -e .[dev]
fi

.venv/bin/cathedral-validator migrate --config config/testnet.toml
.venv/bin/cathedral-validator serve   --config config/testnet.toml --no-json-logs
