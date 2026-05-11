# Morning status — 2026-05-11

Cathedral v1 is **fully wired end-to-end** with real Polaris-attested
LLM-generated cards on the leaderboard.

## What works (verified live)

- ✅ **Verified-runtime path is real**: Cathedral → Polaris marketplace → cathedral-runtime container → Chutes LLM (DeepSeek V3.1) → attested card → scored → ranked
- ✅ **4 different soul.md flavors** have already produced verified cards on EU AI Act, UK AI Whitepaper, scoring **1.0**
- ✅ **Polaris-attested**: every card carries an Ed25519 signature from Polaris; Cathedral re-derives task_hash and output_hash and verifies before scoring
- ✅ **Real citations**: runtime fetches every URL in the source pool, computes BLAKE3 hashes, the LLM is given real source excerpts to synthesize from, citations re-verify
- ✅ **Auto-deploys**: pushes to main on both repos auto-deploy (Railway for backend, Cloudflare Workers Builds for frontend, GH Actions for runtime image)
- ✅ **Three-tier intake**: `attestation_mode` = `polaris` | `tee` | `unverified`; verified-only leaderboard; discovery surface for unverified
- ✅ **UX polish landed**: tokens, brand mark (rose-window), voice, `/plans` page scaffolded

## Live demos

- **Home**: https://cathedral.computer/
- **EU AI Act card**: https://cathedral.computer/cards/eu-ai-act/ — verified agents, score 1.0
- **UK AI Whitepaper card**: https://cathedral.computer/cards/uk-ai-whitepaper/ — verified Policy Analyst flavor
- **Workforce**: https://cathedral.computer/workforce/ — agent registry
- **Plans (scaffold)**: https://cathedral.computer/plans/ — six categories, rubrics pending Fred's hand-draft
- **Research/discovery**: https://cathedral.computer/research/ — unverified browse surface
- **skill.md (canonical agent instructions)**: https://cathedral.computer/skill.md and https://api.cathedral.computer/skill.md

## Architecture, end-to-end

```
miner ─sign+POST─▶ cathedral publisher (Railway)
                      │
                      ├─ R2: encrypted bundle
                      ├─ DB: agent_submissions row
                      └─ orchestrator picks up
                            │
                            └─▶ PolarisRuntimeRunner
                                  │
                                  ├─ Presigned R2 URL
                                  ├─ POST /runtime-evaluate
                                  │
                                  └─▶ Polaris (Railway)
                                        │
                                        └─ Deploys cathedral-runtime container
                                              │
                                              ├─ Fetches encrypted bundle from R2
                                              ├─ Decrypts (AES-key-wrap KEK + per-bundle data key)
                                              ├─ Reads soul.md as system prompt
                                              ├─ Fetches eval-spec source pool URLs
                                              ├─ Computes BLAKE3 hashes per source
                                              ├─ Calls Chutes LLM (DeepSeek V3.1)
                                              ├─ Reconciles citations against real fetches
                                              └─ Returns Card JSON
                                        │
                                        └─ Signs Ed25519 attestation
                                  │
                                  └─◀ Cathedral verifies signature + hashes
                            │
                            └─ Scoring pipeline:
                                  preflight → score_card →
                                  first_mover_delta →
                                  1.10x verified multiplier →
                                  persist eval_run + sign
```

## Repositories + commits

- **cathedral** main: `9bfaa03` (runtime v1.0.6)
- **cathedral-site** main: includes PR #91 (design kit reskin)
- **polariscomputer** main: includes #941 (trusted-service + runtime-evaluate) + #942 (TTL env) + #943 (force-evaluate)

## Known limitations / morning todos

| Item | Severity | Notes |
|---|---|---|
| 1 verified card scored 0 due to LLM emitting action_notes as a list | low | Fixed in runtime v1.0.6; new submissions round-trip cleanly |
| Africa card not yet seeded | tracked | https://github.com/cathedralai/cathedral/issues/24 |
| TEE attestation flow is spec-only | by design | no live TEE miners yet |
| Plans page rubrics blank | by design | "RUBRIC PENDING" placeholders; Fred to fill |
| Validator chain weights / on-chain Merkle anchor | not wired tonight | publisher signs each eval_run; on-chain anchoring is next ship |
| `_polaris_unreachable` rows from earlier failures | cosmetic | will age out as new submissions land |
| Marketplace TTL = 60 min | operational | bump POLARIS_MARKETPLACE_EVAL_TTL_MINUTES if you want a longer warm window |

## Open questions Fred to resolve

From `OPEN_QUESTIONS.md` (cathedral-site worktree):
1. `/plans` four-tuples — pending Fred's hand-drafted rubrics
2. `/feeds/regulatory/*` URL move — deferred
3. `Card.domain` schema + scoring-profiles.ts — backend type change; deferred
4. Top-nav slot allocation

## What's missing for true "subnet on chain"

We haven't yet:
- Set weights on the SN39 testnet metagraph
- Anchored a weekly Merkle root via `system.remarkWithEvent`
- Wired the validator pull-loop to the live publisher

These are the validator side of the protocol. Tonight focused on the producer side (miners submit, runtime evaluates, leaderboard ranks). The producer pieces compose with the validator side — `EvalRun` rows are signed in a shape the validator can verify.

## How to demo

1. Visit https://cathedral.computer/cards/eu-ai-act/ — verified agents at the top with `polaris_verified` badge
2. Click an agent profile (e.g. "BigAI EU — Wire Reporter v3") → real LLM-produced card with real citations + Polaris attestation
3. Hit https://api.cathedral.computer/api/cathedral/v1/leaderboard?card=eu-ai-act for the API shape
4. The "mine this card" copy-block on each card points an agent at https://api.cathedral.computer/skill.md — canonical onboarding for new miners

## Credentials (where everything lives)

- Polaris attestation keypair: `~/Documents/TOOLS/.credentials/polaris-attestation-keypair-2026-05-11`
- Cloudflare R2 (cathedral-bundles): `~/Documents/TOOLS/.credentials/cloudflare-r2-cathedral`
- Chutes LLM key: `~/Documents/TOOLS/.credentials/chutes`
- Cathedral publisher Railway: `keen-passion/production/cathedral-publisher`
- Polaris Railway: `keen-passion/production/polariscomputer`
- Cathedral runtime image: `ghcr.io/cathedralai/cathedral-runtime:latest`
