#!/usr/bin/env bash
# End-to-end smoke test:
#   1. Start stub Polaris (port 9444)
#   2. Pull its public key
#   3. Write a temp validator config pointing at the stub
#   4. Run cathedral-validator (weights disabled, port 9333)
#   5. Submit a claim via cathedral-miner
#   6. Poll /health until claims_verified > 0
#   7. Print the final health snapshot and exit
#
# Requires: pip install -e .[dev] and a python3.11+ venv at .venv/.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [ ! -d .venv ]; then
  echo "set up .venv first: python3.11 -m venv .venv && .venv/bin/pip install -e .[dev]"
  exit 1
fi

PY=".venv/bin/python"
TMPDIR="$(mktemp -d -t cathedral-smoke)"
trap 'echo "[smoke] cleanup"; kill ${POLARIS_PID:-} ${VALIDATOR_PID:-} 2>/dev/null || true; rm -rf "$TMPDIR"' EXIT

echo "[smoke] starting stub polaris on :9444"
PYTHONPATH="$ROOT/src:$ROOT" $PY -m scripts.stub_polaris.server &
POLARIS_PID=$!

# Wait for the stub
for _ in $(seq 1 50); do
  if curl -fsS "http://127.0.0.1:9444/pubkey" >/dev/null 2>&1; then break; fi
  sleep 0.1
done

PUBKEY=$(curl -fsS "http://127.0.0.1:9444/pubkey" | $PY -c "import sys,json; print(json.load(sys.stdin)['public_key_hex'])")
echo "[smoke] polaris pubkey: ${PUBKEY:0:16}..."

mkdir -p "$TMPDIR/data"
cat > "$TMPDIR/validator.toml" <<EOF
[network]
name = "local"
netuid = 1
validator_hotkey = "5Validator"
wallet_name = "default"

[polaris]
base_url = "http://127.0.0.1:9444"
public_key_hex = "$PUBKEY"
fetch_timeout_secs = 10

[http]
listen_host = "127.0.0.1"
listen_port = 9333
bearer_token_env = "CATHEDRAL_BEARER"

[weights]
interval_secs = 60
disabled = true

[storage]
database_path = "$TMPDIR/data/validator.db"

[worker]
poll_interval_secs = 0.5
max_concurrent_verifications = 2

[stall]
after_secs = 600
EOF

cat > "$TMPDIR/miner.toml" <<EOF
miner_hotkey = "5Miner"
owner_wallet = "5Owner_demo"
validator_url = "http://127.0.0.1:9333"
validator_bearer_env = "CATHEDRAL_VALIDATOR_BEARER"
EOF

export CATHEDRAL_BEARER="dev-token"
export CATHEDRAL_VALIDATOR_BEARER="dev-token"

echo "[smoke] migrating validator db"
.venv/bin/cathedral-validator migrate --config "$TMPDIR/validator.toml" >/dev/null

# The default app builder uses BittensorChain. For smoke we patch in MockChain
# via the small launcher in scripts/smoke_launcher.py.
echo "[smoke] starting validator on :9333"
PYTHONPATH="$ROOT/src:$ROOT" $PY -m scripts.smoke_launcher --config "$TMPDIR/validator.toml" &
VALIDATOR_PID=$!

for _ in $(seq 1 50); do
  if curl -fsS "http://127.0.0.1:9333/health" >/dev/null 2>&1; then break; fi
  sleep 0.1
done

echo "[smoke] submitting claim"
.venv/bin/cathedral-miner submit \
  --config "$TMPDIR/miner.toml" \
  --work-unit "card:eu-ai-act" \
  --polaris-agent-id "agt_demo" \
  --polaris-run-ids "run_demo" \
  --polaris-artifact-ids "art_demo"

echo "[smoke] polling for verification"
for i in $(seq 1 60); do
  body=$(curl -fsS http://127.0.0.1:9333/health)
  verified=$(echo "$body" | $PY -c "import sys,json; print(json.load(sys.stdin).get('claims_verified',0))")
  rejected=$(echo "$body" | $PY -c "import sys,json; print(json.load(sys.stdin).get('claims_rejected',0))")
  if [ "$verified" -gt 0 ] || [ "$rejected" -gt 0 ]; then
    echo "[smoke] verified=$verified rejected=$rejected after ${i}s"
    echo "$body" | $PY -m json.tool
    if [ "$verified" -gt 0 ]; then
      echo "[smoke] PASS"
      exit 0
    else
      echo "[smoke] FAIL — claim was rejected"
      exit 1
    fi
  fi
  sleep 1
done

echo "[smoke] FAIL — no resolution after 60 seconds"
curl -fsS http://127.0.0.1:9333/health | $PY -m json.tool
exit 1
