# Validator Runbook

Issue #1 owns this surface. The runbook is canonical: any operator can take over with this document plus credentials.

## Prerequisites

- Linux host (any distro, x86_64 or aarch64)
- Python 3.11 or 3.12
- A Bittensor coldkey + hotkey registered on the target subnet
- An Ed25519 public key from Polaris for evidence verification
- A bearer token for the validator's mutating endpoints

## Install

```bash
git clone https://github.com/bigailabs/cathedral
cd cathedral
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e .
```

This installs three console scripts: `cathedral`, `cathedral-validator`, `cathedral-miner`.

## Configure

Copy `config/testnet.toml` (or `mainnet.toml`), fill in:

- `network.validator_hotkey` — your registered hotkey ss58
- `network.wallet_name` — local bittensor wallet name (default `default`)
- `polaris.public_key_hex` — Polaris signing key
- `http.bearer_token_env` — env var that holds your bearer token

Set the bearer in the shell or the systemd unit, e.g. `CATHEDRAL_BEARER=...`.

## Initialize the database

```bash
cathedral-validator migrate --config config/testnet.toml
```

Idempotent. Safe to re-run.

## Smoke-test the chain connection

Before starting the validator for the first time, confirm wallet + Subtensor
both work:

```bash
cathedral chain-check --config config/testnet.toml
```

Expected output:

```json
{
  "network": "test",
  "netuid": 292,
  "wallet_hotkey": "<your-hotkey-name>",
  "current_block": 5123456,
  "registered": true,
  "metagraph_block": 5123456,
  "metagraph_size": 64
}
```

If `registered` is `false`, register the hotkey on the subnet first
(`btcli s register --netuid <n> --network <net>`). The validator will run
without weights set until registration is detected, and the runbook surfaces
this via `health.registered`.

## Start, stop, restart

Foreground (testing):

```bash
cathedral-validator serve --config config/testnet.toml
```

Systemd (production):

```bash
sudo install -m 0644 scripts/cathedral-validator.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now cathedral-validator
sudo systemctl restart cathedral-validator
sudo systemctl stop cathedral-validator
```

## Logs

```bash
journalctl -u cathedral-validator -f
journalctl -u cathedral-validator --since "1 hour ago" | grep -i error
```

JSON lines by default. Pipe through `jq` for pretty printing.

## Health

`GET /health` is public:

```bash
curl -s http://127.0.0.1:9333/health | jq
cathedral health
```

Fields to watch:

- `registered` — `true` once the validator finds its hotkey on the metagraph
- `current_block` — increases each tick; if it stops, chain reading is stuck
- `last_metagraph_at` — last successful metagraph pull
- `last_weight_set_at` — last successful weight call
- `weight_status` — `healthy` / `blocked_by_stake` / `blocked_by_transaction_error` / `disabled`
- `stalled` — `true` when no metagraph pull for `stall.after_secs` (default 600)
- `claims_pending`, `claims_verifying`, `claims_verified`, `claims_rejected`

## Weight-setting status

| Status | Meaning | Action |
|---|---|---|
| `healthy` | Weights set on schedule | None |
| `blocked_by_stake` | Below permit-stake threshold | Add stake or wait for emissions |
| `blocked_by_transaction_error` | Chain rejected the transaction | Check logs; usually rate-limit, fee, or bad endpoint |
| `disabled` | `weights.disabled = true` in config | Intentional (dry run, handoff window) |

## Registration

```bash
cathedral registration
```

Prints `registered: true` once the metagraph has been read and the validator's hotkey is present.

## Recovery

| Symptom | First step |
|---|---|
| `stalled: true` | `systemctl restart cathedral-validator` |
| `weight_status: blocked_by_stake` | Add stake to validator hotkey |
| `weight_status: blocked_by_transaction_error` | Check chain endpoint, retry next tick |
| HTTP 401 on `/v1/claim` | Bearer mismatch with env var |
| `claims_pending` rising, `claims_verified` flat | Worker not draining — check Polaris fetch logs |
| Disk full on `data/` | Vacuum old `evidence_bundles`, snapshot, restart |

## Handing off

Everything an incoming operator needs:

1. This file
2. `config/<network>.toml` (with hotkey ss58)
3. The hotkey + coldkey files
4. A new bearer token (rotate on every handoff)
5. The Polaris public key (same across operators)

There is no private context beyond credentials. If you find yourself explaining tribal knowledge, file an issue against this runbook.
