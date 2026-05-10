#!/usr/bin/env bash
# Install Cathedral validator under /opt/cathedral with a venv.
# Idempotent — safe to re-run after `git pull`.
set -euo pipefail

PREFIX="${PREFIX:-/opt/cathedral}"
PYTHON="${PYTHON:-python3.11}"

sudo install -d "${PREFIX}"
sudo cp -r . "${PREFIX}/source"
sudo "${PYTHON}" -m venv "${PREFIX}/.venv"
sudo "${PREFIX}/.venv/bin/pip" install --upgrade pip
sudo "${PREFIX}/.venv/bin/pip" install -e "${PREFIX}/source"

echo "Installed to ${PREFIX}"
echo "Configure: /etc/cathedral/<network>.toml"
echo "Service:   scripts/cathedral-validator.service"
echo "Migrate:   ${PREFIX}/.venv/bin/cathedral-validator migrate --config /etc/cathedral/<network>.toml"
