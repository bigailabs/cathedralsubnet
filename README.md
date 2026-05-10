<h1 align="center">Cathedral Subnet</h1>

<p align="center"><em>A subnet that verifies signed evidence about useful work.</em></p>

<p align="center">
  <a href="https://cathedral.computer">cathedral.computer</a>
</p>

---

## What this is

Cathedral is a Bittensor subnet. Miners maintain regulatory and legal intelligence cards via Polaris-hosted Hermes workers. The validator pulls signed Polaris evidence by identifier, verifies it, scores card quality, and sets weights.

This repository is the validator and miner reference implementation in Python.

- **Mainnet:** SN39 (`finney`)
- **Testnet:** SN292 (`test`)

## Status

Pre-1.0. Validator and miner are functional in dry mode. Active work is tracked in [open issues](https://github.com/bigailabs/cathedralsubnet/issues).

## Layout

```
cathedralsubnet/
├── src/cathedral/
│   ├── types.py               # Pydantic wire models (claim, manifest, card)
│   ├── config.py              # ValidatorSettings, MinerSettings
│   ├── chain/                 # Bittensor: metagraph, weights
│   ├── evidence/              # Polaris: fetch, verify (Ed25519), filter
│   ├── cards/                 # Registry, preflight, six-dimension scorer
│   ├── validator/             # FastAPI, sqlite queue, worker, weight loop, watchdog
│   ├── miner/                 # Claim submission client
│   └── cli/                   # `cathedral`, `cathedral-validator`, `cathedral-miner`
├── docs/                      # Runbooks, protocol specs, architecture
├── config/                    # TOML defaults for testnet/mainnet/miner
├── scripts/                   # Systemd unit, install, dev helpers
└── tests/                     # pytest suite (23 tests, all passing)
```

## Quick start (miner)

```bash
git clone https://github.com/bigailabs/cathedralsubnet
cd cathedralsubnet
pip install -e .

# Edit config/miner.toml with your hotkey/wallet/validator URL
export CATHEDRAL_VALIDATOR_BEARER=<token>

cathedral-miner submit \
  --work-unit "card:eu-ai-act" \
  --polaris-agent-id agt_01H... \
  --polaris-run-ids run_01H... \
  --polaris-artifact-ids art_01H...
```

Full guide: [docs/miner/QUICKSTART.md](docs/miner/QUICKSTART.md)

## Running a validator

```bash
pip install -e .[dev]
export CATHEDRAL_BEARER=<token>
cathedral-validator migrate --config config/testnet.toml
cathedral-validator serve   --config config/testnet.toml
```

In another terminal:

```bash
cathedral health        # full snapshot as JSON
cathedral weights       # weight-set status word
cathedral registration  # is the validator on the metagraph
```

Full runbook: [docs/validator/RUNBOOK.md](docs/validator/RUNBOOK.md)

## Architecture

Three asyncio loops sharing a sqlite database and a `Health` snapshot:

1. **HTTP** — `POST /v1/claim` (bearer-protected), `GET /health` (public)
2. **Verification worker** — drains pending claims, runs `EvidenceCollector`, scores, persists
3. **Weight loop** — joins latest scores by hotkey to metagraph uids, normalizes, calls `subtensor.set_weights`

A stall watchdog surfaces silent loops in `/health`.

Detail: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) · [docs/protocol/CLAIM.md](docs/protocol/CLAIM.md) · [docs/protocol/SCORING.md](docs/protocol/SCORING.md)

## License

MIT — see [LICENSE](LICENSE). © 2026 bigailabs.
