#!/usr/bin/env bash
# Build release binaries and lay them out under /opt/cathedral.
# Idempotent. Safe to re-run after `git pull`.
set -euo pipefail

PREFIX="${PREFIX:-/opt/cathedral}"

cargo build --release -p cathedral-validator
cargo build --release -p cathedral-miner
cargo build --release -p cathedral-cli

sudo install -d "${PREFIX}/bin"
sudo install -m 0755 target/release/cathedral-validator "${PREFIX}/bin/"
sudo install -m 0755 target/release/cathedral-miner    "${PREFIX}/bin/"
sudo install -m 0755 target/release/cathedral          "${PREFIX}/bin/"

echo "Installed to ${PREFIX}/bin"
echo "Configure: /etc/cathedral/<network>.toml"
echo "Service:   scripts/cathedral-validator.service"
