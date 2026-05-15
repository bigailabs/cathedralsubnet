# Validator Runbook

Issue #1 owns this surface. The runbook is canonical: any operator can take over with this document plus credentials.

## Prerequisites

- Linux host (any distro, x86_64 or aarch64)
- Python 3.11 or 3.12
- A Bittensor coldkey + hotkey registered on the target subnet
- The Polaris runtime-attestation pubkey (pinned into `polaris.public_key_hex` in TOML; from `kid: polaris-runtime-attestation` in the JWKS document)
- The Cathedral eval-signing pubkey (pinned into `CATHEDRAL_PUBLIC_KEY_HEX` env; from `kid: cathedral-eval-signing` in the JWKS document)
- A bearer token for the validator's own `/v1/claim` endpoint

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

Operators run against SN39 mainnet (`config/mainnet.toml`). The protocol-development copy at `config/testnet.toml` targets SN292 and is retained for that purpose only.

Copy `config/mainnet.toml`, fill in:

- `network.validator_hotkey`: your local Bittensor wallet hotkey NAME (the same value you pass to `btcli` as `--wallet.hotkey`, e.g. `default`). This is NOT the ss58 address. The validator uses the name to open the wallet via `bt.Wallet(name=..., hotkey=<this>)` and reads the ss58 off the on-disk key file. `cathedral chain-check` reports the resolved ss58 if you want to confirm.
- `network.wallet_name`: local Bittensor wallet (coldkey) name (default `cathedral-validator`; change if your local wallet uses a different name)
- `polaris.public_key_hex`: Polaris runtime-attestation pubkey, from `kid: polaris-runtime-attestation` in the JWKS document. Required because `cathedral-validator serve` still constructs the legacy `/v1/claim` worker.
- `http.bearer_token_env`: env var name that holds your local validator bearer token (default `CATHEDRAL_BEARER`)
- `publisher.url`: publisher endpoint (default `https://api.cathedral.computer`)
- `publisher.public_key_env`: env var holding the Cathedral signing pubkey hex (default `CATHEDRAL_PUBLIC_KEY_HEX`)
- `publisher.pull_interval_secs`: how often the pull loop polls `/v1/leaderboard/recent` (default `30.0` seconds)
- `publisher.api_token_env`: optional env var name carrying an `Authorization: Bearer <token>` value the pull loop sends to the publisher. Only meaningful if a future release adds publisher-side enforcement; leave unset today.

Fetch the JWKS document once at setup time and pin the values; do not auto-rotate from the same publisher whose signatures the validator will then trust.

```bash
curl -s https://api.cathedral.computer/.well-known/cathedral-jwks.json | jq
# kid=cathedral-eval-signing -> CATHEDRAL_PUBLIC_KEY_HEX (env)
# kid=polaris-runtime-attestation -> polaris.public_key_hex (TOML)
```

Env vars the validator reads at startup (set in `/etc/cathedral/validator.env` for PM2, or in the shell for foreground runs):

| Var | Required | Purpose |
|---|---|---|
| `CATHEDRAL_BEARER` | yes | Local validator bearer for its own `/v1/claim` endpoint (NOT publisher-read auth). |
| `CATHEDRAL_PUBLIC_KEY_HEX` | yes | 32-byte hex Ed25519 pubkey the publisher signs `EvalRun` projections with. Gates the pull loop: if absent, startup logs `pull_loop_disabled` and the loop is not spawned. Pin from JWKS `kid: cathedral-eval-signing`. |
| `CATHEDRAL_PUBLISHER_TOKEN` | no | Optional, future-facing. Sent as `Authorization: Bearer ...` to the publisher when `[publisher].api_token_env` is configured. The publisher does not currently enforce this. |

## Initialize the database

```bash
cathedral-validator migrate --config config/mainnet.toml
```

Idempotent. Safe to re-run.

## Smoke-test the chain connection

Before starting the validator for the first time, confirm wallet + Subtensor
both work:

```bash
cathedral chain-check --config config/mainnet.toml
```

Expected output:

```json
{
  "network": "finney",
  "netuid": 39,
  "wallet_hotkey": "<your-hotkey-name>",
  "current_block": 5123456,
  "registered": true,
  "metagraph_block": 5123456,
  "metagraph_size": 256
}
```

`wallet_hotkey` here is the local wallet hotkey NAME you set in `network.validator_hotkey`, the same value you pass to `btcli --wallet.hotkey`. The chain-side ss58 is derived from the on-disk key file; `registered` is the result of looking up that ss58 on the subnet's metagraph.

If `registered` is `false`, register the hotkey on the subnet first
(`btcli s register --netuid <n> --network <net>`). The validator will run
without weights set until registration is detected, and the runbook surfaces
this via `health.registered`.

## Start, stop, restart

Foreground (testing):

```bash
cathedral-validator serve --config config/mainnet.toml
```

Production runs under PM2. The `scripts/provision_validator.sh` script installs everything; this section describes day-2 ops.

```bash
sudo -u cathedral pm2 status
sudo -u cathedral pm2 restart cathedral-validator
sudo -u cathedral pm2 stop cathedral-validator
sudo -u cathedral pm2 reload cathedral-validator   # zero-downtime reload after config change
```

The ecosystem file at `/opt/cathedral/ecosystem.config.cjs` defines two apps:

- `cathedral-validator`: the FastAPI server plus four background loops the lifespan wires up: the legacy `/v1/claim` worker, the weight loop, the stall watchdog, and (when `CATHEDRAL_PUBLIC_KEY_HEX` is set) the publisher pull loop.
- `cathedral-updater`: the signed-tag auto-update watchdog (see Auto-update section below).

Boot persistence is set up via `pm2 startup systemd -u cathedral`; PM2 writes a systemd unit (`pm2-cathedral.service`) that re-launches both apps on reboot.

## Auto-update

The `cathedral-updater` PM2 app runs `/opt/cathedral/source/bin/updater.sh` on a 600-second loop. Each tick it:

1. Fetches tags from origin
2. Compares current HEAD tag to latest `v*` tag (sorted by version)
3. If different, verifies the tag's SSH signature against `/opt/cathedral/allowed_signers`
4. Checks out the tag, runs `pip install -e .` in the venv, copies the current ecosystem file, runs validator migrations, and reloads `cathedral-validator`
5. Refuses to update on unsigned or untrusted tags (logs `bad signature` + git's stderr, waits)

Managed hosts that were provisioned before the SN39 mainnet default may still have PM2 args pointing at `/etc/cathedral/testnet.toml`. On startup, the validator treats that exact managed path as legacy unless the host explicitly sets `CATHEDRAL_NETWORK=testnet` or `CATHEDRAL_CONFIG_PATH`. It renders `/etc/cathedral/mainnet.toml` from the current template, preserves the local wallet name, hotkey name, wallet path, and Polaris public key from the old config, records `CATHEDRAL_CONFIG_PATH=/etc/cathedral/mainnet.toml` in `validator.env`, and runs SN39 mainnet from that point forward. Managed mainnet configs also sync the current release burn policy on startup, so stale `forced_burn_percentage` values do not survive signed-tag updates.

Tags are SSH-signed (`gpg.format=ssh`) by the maintainer's `~/.ssh/id_ed25519`. Verification uses `git -c gpg.ssh.allowedSignersFile=/opt/cathedral/allowed_signers tag -v <tag>` and checks the exit code, not output substrings — SSH and GPG produce different "good signature" phrasing and the older substring-grep implementation never matched SSH tags.

Operator setup is handled by `scripts/provision_validator.sh` (step 7b): it copies `etc/cathedral/allowed_signers` from the repo to `/opt/cathedral/allowed_signers` with `chown cathedral:cathedral` and `chmod 0644`. To refresh it manually:

```bash
sudo install -o cathedral -g cathedral -m 0644 \
  /opt/cathedral/source/etc/cathedral/allowed_signers \
  /opt/cathedral/allowed_signers
```

Adding a new maintainer key: append a line to `etc/cathedral/allowed_signers` in the repo (`<principal> <ssh-key-type> <key>`), tag a release, and validators pick it up on the next auto-update.

Release flow (maintainer side):

```bash
git tag -s v1.0.8 -m "validator: ..."
git push origin v1.0.8
# all validators with the allowed_signers file installed pull, install, restart within 10 min
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

JSON lines by default. Pipe through `jq` for pretty printing. The pull loop logs `pull_loop_tick fetched=N persisted=M inner_pulls=K drained=true|false` on every cycle (default cadence 30s). The weight loop logs `weights_set count=N status=healthy`.

## Health

`GET /health` is public:

```bash
curl -s http://127.0.0.1:9333/health | jq
cathedral health
```

Fields to watch:

- `registered`: `true` once the validator finds its hotkey on the metagraph
- `current_block`: increases each tick; if it stops, chain reading is stuck
- `last_metagraph_at`: last successful metagraph pull
- `last_weight_set_at`: last successful weight call
- `last_evidence_pass_at`: last successful pull from publisher (heartbeats every pull_loop tick, default cadence 30s)
- `weight_status`: `healthy` / `blocked_by_stake` / `blocked_by_transaction_error` / `disabled`
- `stalled`: `true` when no metagraph pull for `stall.after_secs` (default 600)
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
| HTTP 401 on `/v1/claim` | `CATHEDRAL_BEARER` env var does not match the request's `Authorization` header (local validator auth only; this is not publisher-read auth) |
| `claims_pending` rising, `claims_verified` flat | Legacy `/v1/claim` worker not draining; check Polaris fetch logs |
| `pull_loop_disabled` in startup logs | `CATHEDRAL_PUBLIC_KEY_HEX` not set in `validator.env`; pull loop is not spawned, validator runs legacy-only |
| `pull_eval_signature_invalid` repeating | Pubkey mismatch; re-fetch `kid: cathedral-eval-signing` from `https://api.cathedral.computer/.well-known/cathedral-jwks.json` |
| `cathedral-updater: bad signature on vX.Y.Z` | Maintainer key not in keyring, or tag truly unsigned; do NOT bypass |
| Disk full on `data/` | Vacuum old `evidence_bundles`, snapshot, restart |

## Handing off

Everything an incoming operator needs:

1. This file
2. `config/<network>.toml` (with `network.validator_hotkey` set to the local wallet hotkey NAME and `polaris.public_key_hex` pinned)
3. The hotkey + coldkey files
4. A new local bearer token for the validator's `/v1/claim` endpoint (rotate on every handoff)
5. The Polaris runtime-attestation pubkey, hex, from `kid: polaris-runtime-attestation` in `/.well-known/cathedral-jwks.json` (same across operators)
6. The Cathedral eval-signing pubkey, hex, from `kid: cathedral-eval-signing` in `/.well-known/cathedral-jwks.json` (same across operators)
7. The maintainer SSH pubkey for tag verification (shipped in repo at `etc/cathedral/allowed_signers`; installed to `/opt/cathedral/allowed_signers`)

There is no private context beyond credentials. If you find yourself explaining tribal knowledge, file an issue against this runbook.

## Provisioning a fresh box

For a clean Polaris CPU box, run `scripts/provision_validator.sh` with the env vars documented in that script (bearer, Cathedral pubkey, Polaris pubkey, local wallet hotkey NAME via `BT_WALLET_HOTKEY`, wallet coldkey name via `BT_WALLET_NAME`, network via `BT_NETWORK` / `CATHEDRAL_NETWORK`, netuid via `BT_NETUID`). Defaults provision SN39 mainnet (`finney`, netuid 39); set `CATHEDRAL_NETWORK=testnet`, `BT_NETWORK=test`, `BT_NETUID=292` to provision the protocol-dev SN292 path instead.

The script:

- Installs Python 3.11, git, nodejs/npm, gpg
- Creates the `cathedral` user and standard dirs
- Clones the repo at the requested release tag
- Renders `/etc/cathedral/<network>.toml` from `config/<network>.toml` (mainnet by default). `network.validator_hotkey` is filled in with the local wallet hotkey NAME (`BT_WALLET_HOTKEY`), the same value passed to `btcli`. The ss58 is read from disk by the bittensor SDK at runtime.
- Renders `/etc/cathedral/validator.env` with chmod 600, including `CATHEDRAL_NETWORK` and `CATHEDRAL_CONFIG_PATH` so PM2 launches the right config file
- Installs PM2 globally, starts the ecosystem, persists across reboots
- Runs `cathedral-validator migrate` to init the DB

The script assumes the wallet has already been created and registered with `btcli` - it does not generate keys.
