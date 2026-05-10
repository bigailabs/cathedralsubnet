# Architecture

## One-line

A miner submits a Polaris agent claim. The validator pulls signed Polaris records, verifies them, scores the resulting card, and sets weights.

## Component map

```
            ┌──────────────┐
miner ─────▶│ /v1/claim    │   bearer-protected POST
            ├──────────────┤
            │ http (axum)  │
            └──────┬───────┘
                   │
          ┌────────▼────────┐    ┌────────────────────┐
          │ evidence loop   │───▶│ cathedral-evidence │
          │ (verifies bg)   │    │ fetch + verify +   │
          └────────┬────────┘    │ filter (Ed25519,   │
                   │             │ BLAKE3)            │
                   ▼             └────────┬───────────┘
          ┌────────────────┐              │
          │ cathedral-cards│◀─────────────┘
          │ preflight +    │       EvidenceBundle
          │ score          │
          └────────┬───────┘
                   │ ScoreParts
                   ▼
          ┌────────────────┐    ┌────────────────────┐
          │ weight loop    │───▶│ cathedral-chain    │
          │ (timer)        │    │ metagraph + weights│
          └────────┬───────┘    └────────────────────┘
                   │
                   ▼
          ┌────────────────┐
          │ /health        │   public, surfaces all of the above
          └────────────────┘
```

## Crate dependency graph

```
cathedral-types          (no deps)
   ▲
   ├── cathedral-chain
   ├── cathedral-evidence
   └── cathedral-cards

cathedral-validator depends on all three
cathedral-miner     depends on cathedral-types only
cathedral-cli       depends on nothing internal — pure HTTP client
```

## What lives where

| Concern | Crate |
|---|---|
| Wire types (claims, manifests, cards) | `cathedral-types` |
| Bittensor reads + weight setting | `cathedral-chain` |
| Polaris fetch + Ed25519 + hash check | `cathedral-evidence` |
| Card registry + scoring + preflight | `cathedral-cards` |
| HTTP, loops, bearer auth, persistence | `cathedral-validator` |
| Miner claim submission CLI | `cathedral-miner` |
| Operator inspection commands | `cathedral-cli` |

## Issue traceability

| Issue | Crate(s) | Module |
|---|---|---|
| #77 verify Polaris worker evidence | `cathedral-evidence` | `lib::EvidenceCollector`, `verify`, `filter` |
| #78 regulatory cards useful and verifiable | `cathedral-cards` | `registry`, `preflight`, `score` |
| #79 validator ops safe and observable | `cathedral-validator` + `docs/validator/RUNBOOK.md` | `auth`, `health`, `loops`, `http` |

## What this repo deliberately omits

- GPU verification, SSH probing, hardware attestation
- Rental flow, billing, k8s/k3s, miner prover daemons
- POM, ModelFactory, cost-collapse marketplace logic
- IP-first miner proof for Polaris-hosted workers
- Public ledger, treasury dashboards, blog content
- Subnet scouting, broad external miner outreach

If a future story crosses into one of those areas, it goes in a sibling repo, not here.
