#!/usr/bin/env bash
# Wrapper: activates repo .venv, sets PYTHONPATH=src, runs submit_agent_bundle.py.
# Use this instead of pasting "source .venv/bin/activate" + PYTHONPATH on separate lines
# (zsh can merge them into activatePYTHONPATH=src).
#
# Examples:
#   ./scripts/miner/submit_agent_bundle.sh --help
#   ./scripts/miner/submit_agent_bundle.sh --bundle .../cathedral-baseline-bundle.zip \
#     --wallet-name Crimzor --wallet-hotkey crim --card-id eu-ai-act \
#     --display-name crimzor-baseline --ssh-host 102.215.78.57 --ssh-user cathedral-probe
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
if [[ -f .venv/bin/activate ]]; then
  # shellcheck source=/dev/null
  source .venv/bin/activate
else
  echo "No .venv here. Run:  cd $ROOT && python3 -m venv .venv && source .venv/bin/activate && pip install -e ." >&2
  exit 1
fi
export PYTHONPATH=src
exec python scripts/miner/submit_agent_bundle.py "$@"
