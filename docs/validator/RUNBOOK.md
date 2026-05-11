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
git clone https://github.com/cathedralai/cathedral
cd cathedral
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e .
```

This installs three console scripts: `cathedral`, `cathedral-validator`, `cathedral-miner`.

## Configure

Copy `config/testnet.toml` (or `mainnet.toml`), fill in:

- `network.validator_hotkey` - your registered hotkey ss58
- `network.wallet_name` - local bittensor wallet name (default `cathedral-validator`)
- `polaris.public_key_hex` - Polaris signing key
- `http.bearer_token_env` - env var that holds your bearer token
- `publisher.url` - publisher endpoint (default `https://api.cathedral.computer`)
- `publisher.public_key_env` - env var holding the Cathedral signing pubkey hex (default `CATHEDRAL_PUBLIC_KEY_HEX`)

Env vars the validator reads at startup (set in `/etc/cathedral/validator.env` for PM2, or in the shell for foreground runs):

| Var | Required | Purpose |
|---|---|---|
| `CATHEDRAL_BEARER` | yes | bearer token for the validator's `/v1/claim` endpoint |
| `CATHEDRAL_PUBLIC_KEY_HEX` | yes | 32-byte hex Ed25519 pubkey the publisher signs with; pull_loop is disabled if absent |
| `CATHEDRAL_PUBLISHER_TOKEN` | no | bearer for the publisher's `/v1/leaderboard/recent` if it requires auth |

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

Production runs under PM2. The `scripts/provision_validator.sh` script installs everything; this section describes day-2 ops.

```bash
sudo -u cathedral pm2 status
sudo -u cathedral pm2 restart cathedral-validator
sudo -u cathedral pm2 stop cathedral-validator
sudo -u cathedral pm2 reload cathedral-validator   # zero-downtime reload after config change
```

The ecosystem file at `/opt/cathedral/ecosystem.config.cjs` defines two apps:

- `cathedral-validator` - the FastAPI server + worker + weight loop + pull loop + stall watchdog
- `cathedral-updater` - the signed-tag auto-update watchdog (see Auto-update section below)

Boot persistence is set up via `pm2 startup systemd -u cathedral`; PM2 writes a systemd unit (`pm2-cathedral.service`) that re-launches both apps on reboot.

## Auto-update

The `cathedral-updater` PM2 app runs `/opt/cathedral/bin/updater.sh` on a 600-second loop. Each tick it:

1. Fetches tags from origin
2. Compares current HEAD tag to latest `v*` tag (sorted by version)
3. If different, verifies the tag is GPG-signed by a maintainer key in the local keyring
4. Checks out the tag, runs `pip install -e .` in the venv, and `pm2 reload cathedral-validator`
5. Refuses to update on unsigned or untrusted tags (logs `bad signature` and waits)

Operator setup:

```bash
# import the cathedral maintainer pubkey on each validator host (one time)
sudo -u cathedral gpg --import /path/to/cathedral-maintainer-pubkey.asc
sudo -u cathedral gpg --lsign-key cathedral-maintainer@cathedral.computer
```

Release flow (maintainer side):

```bash
git tag -s v1.0.8 -m "validator: ..."
git push origin v1.0.8
# all validators with the maintainer key pull, install, restart within 10 min
```

To pin a host to a specific tag (e.g. during incident triage), stop the updater:

```bash
sudo -u cathedral pm2 stop cathedral-updater
sudo -u cathedral pm2 save
```

Restart it with `pm2 start cathedral-updater` once the freeze is over.

## Logs

```bash
sudo -u cathedral pm2 logs cathedral-validator --lines 200
sudo -u cathedral pm2 logs cathedral-updater --lines 50
tail -f /var/log/cathedral/validator.out.log
tail -f /var/log/cathedral/validator.err.log
```

JSON lines by default. Pipe through `jq` for pretty printing. The pull loop logs `pull_loop_tick fetched=N persisted=M` on every cycle; the weight loop logs `weights_set count=N status=healthy`.

## Health

`GET /health` is public:

```bash
curl -s http://127.0.0.1:9333/health | jq
cathedral health
```

Fields to watch:

- `registered` - `true` once the validator finds its hotkey on the metagraph
- `current_block` - increases each tick; if it stops, chain reading is stuck
- `last_metagraph_at` - last successful metagraph pull
- `last_weight_set_at` - last successful weight call
- `last_evidence_pass_at` - last successful pull from publisher (heartbeats every pull_loop tick)
- `weight_status` - `healthy` / `blocked_by_stake` / `blocked_by_transaction_error` / `disabled`
- `stalled` - `true` when no metagraph pull for `stall.after_secs` (default 600)
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
| `stalled: true` | `sudo -u cathedral pm2 restart cathedral-validator` |
| `weight_status: blocked_by_stake` | Add stake to validator hotkey |
| `weight_status: blocked_by_transaction_error` | Check chain endpoint, retry next tick |
| HTTP 401 on `/v1/claim` | Bearer mismatch with env var |
| `claims_pending` rising, `claims_verified` flat | Worker not draining; check Polaris fetch logs |
| `pull_loop_disabled` in startup logs | `CATHEDRAL_PUBLIC_KEY_HEX` not set in validator.env |
| `pull_eval_signature_invalid` repeating | Pubkey mismatch; verify it matches `cathedral.computer/.well-known/cathedral-jwks.json` |
| `cathedral-updater: bad signature on vX.Y.Z` | Maintainer key not in keyring, or tag truly unsigned; do NOT bypass |
| Disk full on `data/` | Vacuum old `evidence_bundles`, snapshot, restart |

## Handing off

Everything an incoming operator needs:

1. This file
2. `config/<network>.toml` (with hotkey ss58)
3. The hotkey + coldkey files
4. A new bearer token (rotate on every handoff)
5. The Polaris public key (same across operators)
6. The Cathedral signing pubkey (same across operators; from `/.well-known/cathedral-jwks.json`)
7. The maintainer GPG pubkey for tag verification (same across operators)

There is no private context beyond credentials. If you find yourself explaining tribal knowledge, file an issue against this runbook.

## Provisioning a fresh box

For a clean Polaris CPU box, run `scripts/provision_validator.sh` with the env vars documented in that script (bearer, Cathedral pubkey, Polaris pubkey, validator hotkey ss58, wallet name, network, netuid). The script:

- Installs Python 3.11, git, nodejs/npm, gpg
- Creates the `cathedral` user and standard dirs
- Clones the repo at the requested release tag
- Renders `/etc/cathedral/testnet.toml` from `config/testnet.toml`
- Renders `/etc/cathedral/validator.env` with chmod 600
- Installs PM2 globally, starts the ecosystem, persists across reboots
- Runs `cathedral-validator migrate` to init the DB

The script assumes the wallet has already been created and registered with `btcli` - it does not generate keys.
