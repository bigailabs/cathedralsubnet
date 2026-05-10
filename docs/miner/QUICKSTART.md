# Miner Quickstart

You operate a Polaris-hosted Hermes worker that maintains one regulatory card. Cathedral verifies signed Polaris evidence about your worker and rewards maintained, useful cards.

## Prerequisites

- Bittensor coldkey + hotkey (registered on SN39 mainnet or SN292 testnet)
- A Polaris account with a deployed agent producing a card
- Your Polaris agent ID (looks like `agt_01H...`)
- The validator URL and bearer token (from the operator running the validator)

## Install

```bash
git clone https://github.com/bigailabs/cathedralsubnet
cd cathedralsubnet
cargo build --release -p cathedral-miner
```

## Configure

Copy `config/miner.toml` and fill in:

- `miner_hotkey` — your hotkey ss58
- `owner_wallet` — your coldkey ss58 (used by Cathedral to filter self-loop usage)
- `validator_url` — the validator endpoint
- `validator_bearer_env` — name of the env var that holds your bearer token

Set the bearer in your shell:

```bash
export CATHEDRAL_VALIDATOR_BEARER=...
```

## Submit a claim

```bash
target/release/cathedral-miner submit \
  --work-unit "card:eu-ai-act" \
  --polaris-agent-id agt_01H1234567890ABCDEF \
  --polaris-run-ids run_01H...,run_01H... \
  --polaris-artifact-ids art_01H...,art_01H...
```

The validator returns `202 Accepted` if the claim shape is valid. Verification is async — check your card on cathedral.computer or run `cathedral health` against the validator URL to see queue depth.

## What gets rewarded

The validator scores your card on six dimensions:

1. **Source quality** — official sources count more than secondary analysis
2. **Freshness** — refresh on the schedule from the card registry
3. **Specificity** — concrete `what_changed` and `why_it_matters`
4. **Usefulness** — action notes and risks
5. **Clarity** — readable summary
6. **Maintenance** — kept current over time

Broken sources, uncited claims, and legal-advice framing fail preflight before scoring.
