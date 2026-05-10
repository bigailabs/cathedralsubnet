#!/usr/bin/env bash
# Run validator against a local sqlite db and a mock bearer.
# Useful for hacking on the loop without standing up Polaris/chain.
set -euo pipefail

mkdir -p data
export CATHEDRAL_BEARER="${CATHEDRAL_BEARER:-dev-token}"
cargo run -p cathedral-validator -- --config config/testnet.toml
