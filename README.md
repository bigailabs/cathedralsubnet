<p align="center"><img src="docs/logo.svg" alt="Cathedral" width="180" /></p>

<h1 align="center">Cathedral Subnet</h1>

<p align="center"><em>A subnet that verifies signed evidence about useful work.</em></p>

<p align="center">
  <a href="https://cathedral.computer">cathedral.computer</a>
</p>

---

## What this is

Cathedral is a Bittensor subnet. Miners maintain regulatory and legal intelligence cards via Polaris-hosted Hermes workers. The validator pulls signed Polaris evidence by identifier, verifies it, scores card quality, and sets weights.

This repository is the validator and miner reference implementation.

- **Mainnet:** SN39 (`finney`)
- **Testnet:** SN292 (`test`)

## Status

Pre-1.0. Validator and miner skeletons are in place. Active development tracked in [open issues](https://github.com/bigailabs/cathedralsubnet/issues).

## Layout

```
cathedralsubnet/
├── crates/
│   ├── cathedral-types/       # Shared types: claims, manifests, evidence
│   ├── cathedral-chain/       # Bittensor: register, metagraph, weights
│   ├── cathedral-evidence/    # Polaris evidence: fetch, verify, filter
│   ├── cathedral-cards/       # Card registry and scoring rules
│   ├── cathedral-validator/   # Validator service: REST, loop, persistence
│   ├── cathedral-miner/       # Miner service: claim submission, health
│   └── cathedral-cli/         # Operator CLI: start, stop, status
├── docs/
│   ├── validator/             # Validator runbook, deploy, recovery
│   ├── miner/                 # Miner setup, claim submission
│   ├── protocol/              # Wire formats, signatures, scoring
│   └── runbooks/              # Operational handoff
├── config/                    # TOML defaults for testnet/mainnet
├── scripts/                   # Deploy, register, weights
└── tests/                     # Integration tests
```

## Lay a stone

Quick start: [docs/miner/QUICKSTART.md](docs/miner/QUICKSTART.md)

```bash
cathedral-cli miner submit \
  --polaris-agent-id agt_01H... \
  --work-unit "card:eu-ai-act"
```

## Running a validator

[docs/validator/RUNBOOK.md](docs/validator/RUNBOOK.md)

```bash
cathedral-cli validator start --config config/mainnet.toml
```

## License

MIT — see [LICENSE](LICENSE). © 2026 bigailabs.
