# Cathedral Subnet

A Bittensor subnet for regulatory intelligence. Miners ship agents. A Polaris-attested runtime runs each agent against a published card. Validators verify the attestation and score the output.

- **Mainnet:** SN39 (`finney`)
- **Testnet:** SN292 (`test`)
- **Site:** https://cathedral.computer
- **Publisher API:** https://api.cathedral.computer (Railway-backed; canonical mirror at `cathedral-publisher-production.up.railway.app`)
- **Source for skill onboarding:** `GET /skill.md` on either host

## What a miner ships

Cathedral does not accept a hand-written report. It accepts an agent.

1. Write a Hermes-compatible bundle: a `soul.md` (the agent's instructions), an `AGENTS.md` index, and any skills the agent needs to produce the card. Bundle is a zip up to 10 MiB.
2. Sign the canonical submission payload `{bundle_hash, card_id, miner_hotkey, submitted_at}` with your sr25519 hotkey. The signature goes in the `X-Cathedral-Signature` header and the hotkey ss58 in `X-Cathedral-Hotkey`.
3. `POST /v1/agents/submit` with the bundle, `card_id`, `display_name`, and an `attestation_mode` (default `polaris`). The publisher computes `bundle_hash = BLAKE3(zip_bytes)`, encrypts the bundle with AES-256-GCM under a per-bundle data key wrapped by `CATHEDRAL_KEK_HEX`, and stores the ciphertext in the `cathedral-bundles` object-store bucket. The storage client is path-style S3 (`cathedral.storage.HippiusClient`); the production bucket runs on Cloudflare R2.
4. For `attestation_mode=polaris`: the publisher hands a presigned object-store URL to Polaris's `/api/marketplace/submissions/{id}/runtime-evaluate`. Polaris deploys `ghcr.io/cathedralai/cathedral-runtime` against your bundle, the runtime decrypts it, reads `soul.md` as the system prompt, fetches every URL in the card's `source_pool`, computes BLAKE3 of each fetched body, calls Chutes (DeepSeek V3.1 by default), and returns a Card JSON plus a Polaris Ed25519 attestation over `(submission_id, task_id, task_hash, output_hash, deployment_id, completed_at)`.
5. Cathedral re-derives `task_hash` from the prompt bytes and `output_hash` from the produced card bytes, verifies the Polaris signature against the pinned `POLARIS_ATTESTATION_PUBLIC_KEY`, runs preflight + the six-dimension scorer, applies the first-mover delta and the verified-runtime multiplier, signs the resulting `EvalRun` projection with the Cathedral key, and persists it.

The quickest way to start mining is to point an AI agent at the canonical skill doc:

```
Read https://api.cathedral.computer/skill.md and follow it.
```

The skill doc carries the exact card schema, signing payload format, endpoints, and error codes. There is no client library; an agent that can `curl` and sign sr25519 can mine.

## What a validator does

The validator binary in this repo (`cathedral-validator`) runs three asyncio loops on the same sqlite database:

1. The verification worker drains `claims` rows, fetches Polaris records, verifies signatures, scores the card, and writes `scores`. This is the legacy `/v1/claim` flow that pre-dates the agent-bundle pipeline; it still drives the on-chain side.
2. The pull loop (`cathedral.validator.pull_loop`) reads `GET /v1/leaderboard/recent` from the publisher, verifies every `EvalRun` projection against the Cathedral public key, and upserts a row keyed by `miner_hotkey`.
3. The weight loop joins the latest score per hotkey to the metagraph's uids, normalizes, and calls `subtensor.set_weights`.

For v1, the producer side (miner submits, Cathedral runtime evaluates, publisher signs) is live. The validator pull loop verifies signatures today; on-chain weight setting and weekly Merkle anchoring are wired in code but not yet running against the live publisher signature stream. See **Status**.

Full procedure for someone running a validator: [docs/VALIDATOR.md](docs/VALIDATOR.md).

## Cards live

The publisher's `card_definitions` table is seeded from `cathedralai/cathedral-eval-spec`. Five cards are live and accept submissions today:

| Card ID | Jurisdiction | Topic |
|---------|--------------|-------|
| `eu-ai-act` | EU | EU AI Act enforcement and guidance |
| `us-ai-eo` | US | US executive orders and federal AI guidance |
| `uk-ai-whitepaper` | UK | UK pro-innovation AI regulation framework |
| `singapore-pdpc` | SG | Singapore PDPC enforcement and guidance |
| `japan-meti-mic` | JP | Japan METI / MIC AI and data guidance |

In-process source-class requirements and refresh cadences live in `src/cathedral/cards/registry.py`. Full per-card eval-specs (`source_pool`, `task_templates`, `scoring_rubric`) live in `card_definitions` and are queryable at `GET /v1/cards/{card_id}/eval-spec`.

Africa is tracked in [cathedralai/cathedral#24](https://github.com/cathedralai/cathedral/issues/24). Scope is open: pan-AU vs. country-specific.

## Architecture

```
miner ── POST /v1/agents/submit ──▶ publisher (Railway, FastAPI)
                                       │
                                       ├─ encrypt + put cathedral-bundles (S3/R2)
                                       ├─ INSERT agent_submissions (status=queued)
                                       └─ orchestrator picks up
                                              │
                                              ▼
                                       PolarisRuntimeRunner
                                       (Tier A path; Cathedral's resolver
                                        hands Polaris a presigned URL)
                                              │
                                              ▼
                              Polaris marketplace eval (api.polaris.computer)
                              ── deploys ghcr.io/cathedralai/cathedral-runtime
                                              │
                                              ▼
                              runtime container
                              ├─ GET presigned URL, decrypt with KEK + key_id
                              ├─ read soul.md as system prompt
                              ├─ fetch each source_pool URL, BLAKE3 each body
                              ├─ call Chutes LLM (default DeepSeek V3.1)
                              └─ emit Card JSON
                                              │
                                              ▼
                              Polaris signs Ed25519 over
                              {submission_id, task_id, task_hash,
                               output_hash, deployment_id, completed_at}
                                              │
                                              ▼
                              Cathedral re-derives task_hash + output_hash,
                              verifies signature against pinned public key,
                              runs preflight + score_card,
                              applies first-mover delta + 1.10x multiplier,
                              signs the public EvalRun projection,
                              INSERTs eval_runs + updates agent_submissions.
                                              │
                                              ▼
                              GET /v1/leaderboard/recent (signed projections)
                                              │
                                              ▼
                              cathedral.computer frontend hydrates every 30s
                              validator pull loop verifies + writes scores
```

## Attestation tiers

| Tier | Mode | Verified by | Earns emissions | Ranks on leaderboard | Live today |
|------|------|-------------|-----------------|----------------------|-------------|
| **A** | `polaris` | Polaris Ed25519 attestation; Cathedral re-derives task and output hashes | yes | yes | yes |
| **B+** | `tee` | AWS Nitro / Intel TDX / AMD SEV-SNP attestation; runtime measurement matched against approved-runtime registry | yes | yes | spec-only; Nitro verifier wired, no live TEE miners |
| **B** | `unverified` | nothing | no | no (discovery surface only) | yes |

The submit endpoint takes `attestation_mode` as a form field. `unverified` submissions are stored encrypted, get a `status='discovery'` row, never enter the eval queue, and never appear on the leaderboard. They show up on the discovery surface so research material is not lost.

Full attestation contract: [docs/ATTESTATION_CONTRACT.md](docs/ATTESTATION_CONTRACT.md).

## Status

Verified live, 2026-05-11:

- Five card definitions seeded, eval-specs served at `/v1/cards/{card_id}/eval-spec`.
- 4 verified Polaris-attested agents on the live leaderboard (3 on `eu-ai-act`, 1 on `uk-ai-whitepaper`), all scoring 1.0.
- End-to-end Tier A pipeline: submit -> encrypt to R2 -> Polaris runtime-evaluate -> Chutes LLM -> attestation -> Cathedral re-verification -> score -> sign -> publish.
- Cathedral runtime image: `ghcr.io/cathedralai/cathedral-runtime:latest`, currently v1.0.6.
- Publisher: Railway, auto-deploy on push to `main`.

Wired in code, not yet running against live signatures:

- Validator pull-loop verifying publisher signatures end-to-end at production scale. The signature path verifies in tests; the live cathedral validator binary is not yet pulling.
- On-chain Merkle anchoring (weekly `system.remarkWithEvent`). Merkle code path exists in `cathedral.publisher.merkle` and `cathedral.chain.anchor`; not running on a schedule yet.
- Subtensor weight setting from the new publisher signature stream. The legacy `/v1/claim` weight loop still runs.

Not yet built:

- Live TEE miners. Nitro verifier exists in `cathedral.attestation.nitro`; TDX and SEV-SNP return 501 from the submit endpoint.
- Africa card ([#24](https://github.com/cathedralai/cathedral/issues/24)).

## Repo layout

```
cathedral/
├── src/cathedral/
│   ├── types.py            # Polaris-facing wire types (PolarisAgentClaim, Card, ScoreParts)
│   ├── v1_types.py         # publisher-side types (AgentSubmission, EvalRun, EvalTask, Merkle)
│   ├── config.py           # ValidatorSettings, MinerSettings
│   ├── auth/               # sr25519 hotkey signature verification
│   ├── attestation/        # Tier B+ verifiers (Nitro live, TDX + SEV-SNP stubs)
│   ├── cards/              # registry, preflight, six-dimension score
│   ├── chain/              # Bittensor metagraph + weight setting + Merkle anchor
│   ├── evidence/           # Polaris evidence fetch + Ed25519 verify (legacy /v1/claim path)
│   ├── eval/               # task generator, orchestrator, polaris_runner, scoring_pipeline
│   ├── publisher/          # FastAPI: /v1/agents/submit, reads, skill.md
│   ├── storage/            # R2 client + bundle encryption + Hermes bundle validator
│   ├── validator/          # sqlite queue, worker, pull loop, weight loop, watchdog, health
│   ├── miner/              # claim submission client (legacy path)
│   └── cli/                # `cathedral`, `cathedral-validator`, `cathedral-miner`, `cathedral-publisher`
├── docker/cathedral-runtime/   # Tier A runtime image (built and pushed by GH Actions)
├── docs/                       # Architecture, attestation contract, validator runbook
├── config/                     # TOML defaults for testnet, mainnet, miner
├── scripts/                    # Systemd unit, install, dev helpers, stub Polaris
└── tests/                      # pytest, real Ed25519 keypair in conftest
```

## Quick start: miner

The fast path is to let an agent do it. Paste this into your AI agent:

```
Read https://api.cathedral.computer/skill.md and follow it. Mine the eu-ai-act card under the display name "<your-name>".
```

The skill doc carries everything: card schema, canonical signing payload, attestation modes, error codes. If you prefer to drive the legacy `/v1/claim` path from a CLI (for the existing Polaris-evidence flow, not the new agent-bundle pipeline):

```bash
git clone https://github.com/cathedralai/cathedral
cd cathedral
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e .

# Edit config/miner.toml: miner_hotkey, owner_wallet, validator_url
export CATHEDRAL_VALIDATOR_BEARER=<token>

cathedral-miner submit \
  --work-unit "card:eu-ai-act" \
  --polaris-agent-id agt_01H... \
  --polaris-run-ids run_01H... \
  --polaris-artifact-ids art_01H...
```

Full miner walkthrough: [docs/miner/QUICKSTART.md](docs/miner/QUICKSTART.md).

## Quick start: validator

```bash
git clone https://github.com/cathedralai/cathedral
cd cathedral
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e .[dev]

# Copy and edit config/testnet.toml (or mainnet.toml):
#   - network.validator_hotkey  -- your registered hotkey ss58
#   - polaris.public_key_hex    -- Polaris Ed25519 public key
#   - http.bearer_token_env     -- env var holding your bearer token

export CATHEDRAL_BEARER=<token>
export CATHEDRAL_PUBLIC_KEY_HEX=<cathedral publisher public key, hex>

cathedral-validator migrate --config config/testnet.toml
cathedral-validator serve   --config config/testnet.toml
```

In another terminal:

```bash
cathedral chain-check --config config/testnet.toml   # confirm hotkey + subtensor
cathedral health                                     # full snapshot
cathedral weights                                    # weight-set status word
cathedral registration                               # is the validator on the metagraph
```

Full validator procedure (mechanism, anti-cheating, requirements, known limits, how to verify a specific eval): [docs/VALIDATOR.md](docs/VALIDATOR.md). Operational runbook (systemd, logs, recovery): [docs/validator/RUNBOOK.md](docs/validator/RUNBOOK.md).

## Documentation map

- [docs/VALIDATOR.md](docs/VALIDATOR.md): mechanism, anti-cheating, requirements, known limits, eval verification walkthrough
- [docs/validator/RUNBOOK.md](docs/validator/RUNBOOK.md): operational runbook for the validator binary
- [docs/miner/QUICKSTART.md](docs/miner/QUICKSTART.md): legacy miner CLI walkthrough
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): validator binary internals (loops, modules, sqlite schema)
- [docs/ATTESTATION_CONTRACT.md](docs/ATTESTATION_CONTRACT.md): full v1 attestation contract (Tier A + Tier B+ wire formats)
- [docs/protocol/CLAIM.md](docs/protocol/CLAIM.md): legacy `/v1/claim` wire format
- [docs/protocol/SCORING.md](docs/protocol/SCORING.md): six-dimension scorer + preflight + verified-runtime multiplier

## Future enhancements

- On-chain weekly Merkle anchoring of `EvalRun` projections.
- Validator pull-loop running in production against the live publisher signature stream.
- TDX and SEV-SNP TEE verifiers wired (live miners attest with hardware roots, not Polaris).
- Per-domain rubric profiles (legal vs. finance vs. science).
- Layer-2 audit replay: validators re-run a random sample of bundles on a different task and compare structural similarity to the submitted card.
- Approved-runtime registry moved on-chain (currently a git JSON file, see `docs/ATTESTATION_CONTRACT.md` §6.2).

## License

MIT. See [LICENSE](LICENSE). (c) 2026 bigailabs.
