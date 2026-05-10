# Validator Runbook

Issue #79 owns this surface. The runbook is canonical: any operator can take over with this document plus credentials.

## Prerequisites

- Linux host (any distro, x86_64 or aarch64)
- Rust toolchain (`rustup`, stable)
- A Bittensor coldkey + hotkey registered on the target subnet
- An Ed25519 public key from Polaris for evidence verification
- A bearer token for the validator's mutating endpoints

## Install

```bash
git clone https://github.com/bigailabs/cathedralsubnet
cd cathedralsubnet
cargo build --release -p cathedral-validator
```

Binary lands at `target/release/cathedral-validator`.

## Configure

Copy `config/testnet.toml` (or `mainnet.toml`), fill in:

- `network.validator_hotkey` — your registered hotkey ss58
- `polaris.public_key_hex` — Polaris signing key
- `http.bearer_token_env` — name of the env var that holds your bearer token

Set the bearer in the shell or systemd unit, e.g. `CATHEDRAL_BEARER=...`.

## Start, stop, restart

Foreground (testing):

```bash
target/release/cathedral-validator --config config/testnet.toml
```

Systemd (production):

```bash
sudo cp scripts/cathedral-validator.service /etc/systemd/system/
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

JSON lines by default — pipe through `jq` for pretty printing.

## Health

The validator exposes `GET /health` (no auth):

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
- `stalled` — `true` when no metagraph pull for 600 seconds

## Weight-setting status

| Status | Meaning | Action |
|---|---|---|
| `healthy` | Weights set on schedule | None |
| `blocked_by_stake` | Below permit-stake threshold | Add stake or wait for emissions |
| `blocked_by_transaction_error` | Chain rejected the transaction | Check logs; usually rate-limit or fee |
| `disabled` | `weights.disabled = true` in config | Intentional (handoff, dry run) |

## Registration

```bash
cathedral registration
```

Prints `registered: true` once the metagraph has been read and the validator's hotkey appears.

## Recovery

| Symptom | First step |
|---|---|
| `stalled: true` | Restart: `systemctl restart cathedral-validator` |
| `weight_status: blocked_by_stake` | Add stake to validator hotkey |
| `weight_status: blocked_by_transaction_error` | Check chain endpoint, retry next tick |
| HTTP 401 on `/v1/claim` | Bearer token mismatch with `CATHEDRAL_BEARER` env |
| Disk full on `data/` | Vacuum old verified claims, snapshot, restart |

## Handing off

Everything an incoming operator needs:

1. This file
2. `config/<network>.toml` (with hotkey ss58)
3. The hotkey + coldkey files
4. The bearer token (rotate it on handoff; old operator can no longer post claims)
5. The Polaris public key (same across operators)

There is no private context beyond credentials. If you find yourself explaining tribal knowledge, file an issue against this runbook instead.
