# Validator notes

This document is the structural reference for validators. It explains what the eval pipeline does, what each anti-cheating mechanism protects against, what a validator operator needs to run one, and how to verify any specific eval on the live publisher.

The operational runbook (systemd unit, log filtering, weight-status table, recovery steps) lives in [validator/RUNBOOK.md](validator/RUNBOOK.md). This file is the conceptual companion: the "why each check exists" doc that lets a new operator reason about the system end-to-end.

## Mechanism

The eval pipeline runs inside the publisher process (`cathedral.publisher.app`) once a miner has submitted a bundle. For each submission with `attestation_mode='polaris'`, status `queued`, the orchestrator (`cathedral.eval.orchestrator.EvalOrchestrator`) does the following:

1. **Promote to `evaluating`.** Single-tick state transition so a separate `run_once()` call observes the new state. Confirms the row is not double-claimed.
2. **Generate the eval task.** `cathedral.eval.task_generator.generate_task` produces a deterministic `EvalTask` per `(card_id, epoch, round_index)`. The prompt is the card's task template hydrated with the current `source_pool`. `task_hash = BLAKE3(prompt.encode('utf-8'))` is the hash the attestation will bind to.
3. **Resolve the runner.** Dispatch by `attestation_mode`:
   - `polaris` -> `PolarisRuntimeRunner` (Tier A, the live path)
   - legacy `bundle` -> `BundleCardRunner` (BYO-compute, reads `artifacts/last-card.json` from the bundle, no attestation)
   - test modes (`stub*`) -> in-process stubs
4. **Dispatch the eval.** `PolarisRuntimeRunner.run` does the following in order:
   - Resolve a presigned URL for the encrypted bundle on the `cathedral-bundles` bucket via `HippiusPresignedUrlResolver` (boto3 `generate_presigned_url`, default 1-hour expiry).
   - `POST /api/marketplace/submissions/{POLARIS_CATHEDRAL_RUNTIME_SUBMISSION_ID}/runtime-evaluate` to Polaris with `{task: prompt, task_id: "cathedral-{card_id}-e{epoch}r{round_index}", timeout_seconds, env_overrides: {CARD_ID, MINER_BUNDLE_URL, CATHEDRAL_BUNDLE_KEK, CATHEDRAL_BUNDLE_KEY_ID, CHUTES_API_KEY}}`.
   - Polaris deploys `ghcr.io/cathedralai/cathedral-runtime:latest` against the bundle. The runtime fetches the presigned URL, decrypts using `CATHEDRAL_BUNDLE_KEK` plus the per-bundle wrapped data key in `CATHEDRAL_BUNDLE_KEY_ID`, reads `soul.md` as the system prompt, fetches every URL in the card's `source_pool`, computes BLAKE3 of each fetched body, calls Chutes (default `deepseek-ai/DeepSeek-V3.1`), reconciles citations against real fetches, and returns Card JSON.
   - Polaris signs an Ed25519 attestation `{version: "polaris-v1", payload: {submission_id, task_id, task_hash, output_hash, deployment_id, completed_at}, signature, public_key}` and returns it alongside the runtime output.
5. **Verify the Polaris attestation.** `PolarisRuntimeRunner._verify_attestation` recomputes:
   - `expected_task_hash = BLAKE3(task.prompt.encode("utf-8"))`; must equal `payload.task_hash`.
   - `expected_output_hash = BLAKE3(output_bytes)` where `output_bytes = base64-decode(response.output)`; must equal `payload.output_hash`.
   - `payload.task_id` equals the id Cathedral sent.
   - `payload.submission_id` equals the configured `POLARIS_CATHEDRAL_RUNTIME_SUBMISSION_ID`.
   - Response `public_key` equals the configured `POLARIS_ATTESTATION_PUBLIC_KEY` (pinned, no rotation via response).
   - `Ed25519.verify(signature, canonical_json(payload))` against the pinned key.
   - Any mismatch raises `PolarisAttestationError` and the orchestrator marks the run as a runner failure; no score persisted.
6. **Preflight.** `cathedral.cards.preflight.preflight(card)` (`src/cathedral/cards/preflight.py`):
   - Citations non-empty.
   - `no_legal_advice` is the literal boolean `true`.
   - `summary`, `what_changed`, `why_it_matters` non-empty after strip.
   - Every citation `200 <= status < 400`.
   - No legal-advice framing in `summary + action_notes + why_it_matters` (substring match against `LEGAL_ADVICE_PHRASES`: `"you should"`, `"we recommend that you"`, `"our advice is"`, `"as your lawyer"`, `"this constitutes legal advice"`).
   - On failure: the eval row is persisted with `weighted_score=0` and an `errors` entry; no rank update.
7. **Score.** `cathedral.cards.score.score_card(card, registry_entry)` produces a six-dimension `ScoreParts` in `[0.0, 1.0]` each:
   - `source_quality` (weight 0.30): share of citations from `OFFICIAL_SOURCE_CLASSES` (`government`, `regulator`, `court`, `parliament`, `law_text`, `official_journal`), plus up to `+0.20` for covering the registry entry's `required_source_classes`.
   - `maintenance` (weight 0.20): bands by `age_hours / cadence`. `<=1` gives 1.0, `<=2` gives 0.6, `<=4` gives 0.2, else 0.0.
   - `freshness` (weight 0.15): continuous decay. `ratio <= 1.0` gives 1.0, `ratio >= 4.0` gives 0.0, linear in between.
   - `specificity` (weight 0.15): length bands of `what_changed + why_it_matters`. `<100` gives 0.2, `<400` gives 0.6, `<1500` gives 1.0, else 0.7.
   - `usefulness` (weight 0.10): `+0.5` if `action_notes`, `+0.3` if `risks`, `+0.2` if `confidence > 0.5`, capped at 1.0.
   - `clarity` (weight 0.10): `summary` length in `[40, 800]` gives at least 0.4, with 1.0 awarded if 1-6 sentences.
   - Weights sum to 1.0; tunable by subclassing `cathedral.types.ScoreParts.weighted`.
8. **First-mover delta.** `cathedral.eval.scoring.first_mover_multiplier` (in `src/cathedral/eval/scoring.py`):
   - First mover for `(card_id, metadata_fingerprint)` -> multiplier 1.0.
   - Late mover beating incumbent by `+0.05` -> multiplier 1.0.
   - Late mover outside the 30-day window -> multiplier 1.0.
   - Otherwise -> multiplier 0.50.
9. **Verified-runtime multiplier.** `polaris_verified = polaris_attestation is not None or bool(polaris_agent_id)`; multiplier `1.10` if verified else `1.00`. Applied after the first-mover delta, then `weighted_final = min(1.0, weighted_after_first_mover * verified_multiplier)`.
10. **Hash the output card.** `output_card_hash = BLAKE3(canonical_json(output_card_json))`. The pipeline hashes the literal dict the publisher both serves and stores, not a Pydantic re-render. This is what downstream verifiers pin.
11. **Sign the public projection.** `EvalSigner.sign` over `canonical_json` of:

    ```
    {
      id, agent_id, agent_display_name, card_id,
      output_card, output_card_hash,
      weighted_score, polaris_verified, ran_at
    }
    ```

    Canonical-JSON rules from `cathedral.v1_types.canonical_json`: sorted keys, no whitespace, UTF-8, with `signature`, `cathedral_signature`, and `merkle_epoch` excluded from the signed bytes. Signing key is `CATHEDRAL_EVAL_SIGNING_KEY` (32-byte raw Ed25519 private key, hex).

    See **Known limitations** for the served-vs-signed projection mismatch around `polaris_attestation`.

12. **Persist + rank.** Insert into `eval_runs`. Recompute the submission's 30-day rolling average and `current_rank` within the card. Update `agent_submissions`.

The signed projection is the wire shape served by `GET /v1/leaderboard/recent` and `GET /v1/agents/{id}` (in `recent_evals[].cathedral_signature`). A validator's only trust input is the Cathedral public key and the projection bytes; everything else is derivable.

### The signed projection (EvalOutput)

Fields and rules:

| Field | Source | Notes |
|-------|--------|-------|
| `id` | uuid4 | eval_run row id |
| `agent_id` | `agent_submissions.id` | the miner's submission, not their hotkey |
| `agent_display_name` | submission | persisted for downstream UI without a join |
| `card_id` | submission | one of the registry entries |
| `output_card` | runtime response | the literal Card dict the publisher serves |
| `output_card_hash` | `BLAKE3(canonical_json(output_card))` | pinned so the projection is self-verifying |
| `weighted_score` | scorer + multipliers | post-clip, in `[0.0, 1.0]` |
| `polaris_verified` | runner | reflects whether a Polaris attestation was verified |
| `ran_at` | `_ms_iso(datetime.now(UTC))` | ISO-8601 UTC, millisecond precision, `Z` suffix |
| `cathedral_signature` | `EvalSigner.sign(...)` | base64 Ed25519, excluded from canonical bytes |
| `merkle_epoch` | weekly close job | excluded from canonical bytes; appended post-anchor |

The canonical bytes for verification are `json.dumps(dict_minus_excluded, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")`. Exact reference in `cathedral.v1_types.canonical_json`. The validator implementation that does this is `cathedral.validator.pull_loop.verify_eval_output_signature`.

The Cathedral public key is not yet served at a JWKS endpoint. For v1, set `CATHEDRAL_PUBLIC_KEY_HEX` in the validator environment; pin it from the publisher operator out of band. A `GET /.well-known/cathedral-jwks.json` endpoint is on the roadmap.

## Anti-cheating

Each row below names a concrete attack and the mechanism that catches it. Citations point at the file enforcing the check.

| Attack | Mitigation | Where |
|--------|------------|-------|
| Hand-written cards posing as agent output | Tier A: Polaris attestation binds `output_hash` to the runtime's actual emission. Tier B+: TEE attestation binds output to a measured runtime. Tier B (`unverified`): never reaches the leaderboard. | `src/cathedral/eval/polaris_runner.py` `_verify_attestation`; `src/cathedral/publisher/submit.py` mode dispatch |
| Copying another miner's card | Submission-time exact-bundle-hash and fuzzy display-name checks at intake; first-mover delta penalty (0.50x) for late copies that fail to beat the incumbent by 0.05 within 30 days. | `src/cathedral/publisher/similarity.py`; `src/cathedral/eval/scoring.py::first_mover_multiplier` |
| Citation hallucination | The runtime fetches every citation URL and computes `BLAKE3(bytes)` server-side. Preflight rejects any citation outside HTTP 200-399. | `docker/cathedral-runtime/server.py` (fetch + hash), `src/cathedral/cards/preflight.py::BrokenSourceError` |
| Legal-advice framing | Preflight scans the concatenated summary, action notes, and why-it-matters for prohibited phrases. | `src/cathedral/cards/preflight.py::LEGAL_ADVICE_PHRASES` |
| Bundle tampering at rest | Per-bundle AES-256-GCM data key wrapped under a master KEK; ciphertext stored in `cathedral-bundles`; nonce bound to the wrapped key id. Mismatched nonces fail decrypt. | `src/cathedral/storage/crypto.py::encrypt_bundle` / `decrypt_bundle` |
| Replay of an old attestation against a new submission | Per-attestation `submission_id`, `task_id`, `task_hash`, `output_hash`, `deployment_id`, `completed_at` are all in the signed payload; Cathedral re-derives `task_hash` and `output_hash` and rejects any mismatch. The cathedral signature also binds to `ran_at`. | `src/cathedral/eval/polaris_runner.py::_verify_attestation` |
| Sybil display-name collision | NFKC-normalized + lowercased + whitespace-collapsed display names; Levenshtein ratio >= 0.85 against names from the last 7 days rejects. Fingerprint = `BLAKE3(display_name_norm | bundle_size_bucket_1KiB)` collisions across hotkeys rejected. | `src/cathedral/publisher/similarity.py::run_similarity_check` |
| Same hotkey, same bundle re-upload | `idx_agent_unique` UNIQUE index on `(miner_hotkey, card_id, bundle_hash)` -> 409. | `src/cathedral/publisher/repository.py` (schema), 409 mapping in `submit.py` |
| Cross-hotkey bundle theft (same zip bytes, different hotkey) | Exact bundle-hash duplicate check before any storage write. 409. | `similarity.run_similarity_check` |
| Backdated `submitted_at` (gaming first-mover) | Server clock is the sole source of truth for the persisted `submitted_at` and `first_mover_at`. The client-supplied value is used only to verify the hotkey signature; a ±5 minute skew window rejects obvious back/forward-dating. | `src/cathedral/publisher/submit.py` CRIT-1 |
| Output card with miner-supplied identity fields | The scoring pipeline overrides `id`, `worker_owner_hotkey`, and `polaris_agent_id` with server-trusted values before the card is validated or hashed. | `src/cathedral/eval/scoring_pipeline.py::score_and_sign` CRIT-9 |
| Polaris substituting its own public key | Cathedral pins `POLARIS_ATTESTATION_PUBLIC_KEY` and compares the response's `public_key` byte-for-byte before verifying the signature. No rotation via response. | `polaris_runner.py` `_verify_attestation` step 5 |

The honest gap: a sophisticated miner who buys real GPUs, runs an approved Hermes build, and feeds it deliberately misleading source material will pass every attestation check. The attestation only proves "this approved runtime produced this output for this task." Source-quality scoring is what catches misleading content, and that's a scoring concern, not an attestation one.

## Requirements for running a validator

### Hardware

A small CPU-only box. No GPU.

- **RAM:** 4 GB minimum, 8 GB recommended
- **Disk:** 50 GB SSD
- **CPU:** 2 vCPU minimum, 4 vCPU comfortable
- **Network:** stable outbound HTTPS. The validator polls every 12s and pulls ~10 KB per round.

### Prerequisites

- A Bittensor sr25519 hotkey registered on **SN39** (mainnet) or **SN292** (testnet).
- A Linux host (any distro, x86_64 or aarch64).
- **Python 3.11 or 3.12.** Newer Python versions are not yet tested.
- SQLite (default) or PostgreSQL for the local store. SQLite is fine for v1.

### Networking

- **Inbound: nothing public required.** The validator binds an HTTP admin/health server on `0.0.0.0:9333` by default (configurable via `http.listen_host` / `http.listen_port`). Bind it to `127.0.0.1` if you only want local access, or leave on `0.0.0.0` and firewall it. You do not need this port reachable from the public internet — Cathedral never connects to you. The bearer-protected admin endpoints are for your own ops tooling.
- **Outbound: HTTPS 443 only.** The validator initiates outbound connections to `api.cathedral.computer` (publisher) and your configured subtensor endpoint. No other outbound dependencies.
- **No NAT / port-forwarding required.** Unlike miner-style subnets, Cathedral validators don't receive inbound traffic from miners or other validators. Pull-only.

### What you do NOT need

- A GPU. The validator does no model inference.
- Write access to the `cathedral-bundles` bucket. Validators verify signed projections from the publisher; they do not re-decrypt bundles.
- Polaris API tokens. Those are publisher-side credentials.
- The KEK (`CATHEDRAL_KEK_HEX`). Validators never see plaintext bundles.

## Quickstart

### 1. Install

```bash
git clone https://github.com/cathedralai/cathedral
cd cathedral
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
```

This installs four console scripts: `cathedral`, `cathedral-validator`, `cathedral-miner`, `cathedral-publisher`.

### 2. Fetch the public keys

Cathedral and Polaris pubkeys are published at the publisher's JWKS endpoint. Pin them as env vars on your validator host so all signature verification runs locally:

```bash
curl -s https://api.cathedral.computer/.well-known/cathedral-jwks.json
```

Today's values (May 2026 — refresh from the URL above before pinning so you catch any rotation):

```bash
# Cathedral's signing key (verifies every EvalRun projection)
export CATHEDRAL_PUBLIC_KEY_HEX=10890a66aa752479cb3b634f366d7bd27c374324d83f88d2d6b69ab066f25e26

# Polaris attestation pubkey (pinned for context; validators do not
# verify Polaris signatures themselves — the publisher does that)
export POLARIS_ATTESTATION_PUBLIC_KEY=50b8a077ab857c91a9b4f2b94295e81f0f01e4ec1fa5b3e9fd4073ea00def24c
```

### 3. Set the bearer token

Set `CATHEDRAL_BEARER` to any non-empty string. The validator uses it to build the `Authorization` header for publisher reads. The publisher does not enforce authentication on `/v1/leaderboard/recent` yet, so the value does not matter today; it must just be non-empty so the validator boots cleanly. Server-side enforcement will land in a later release and tokens will be re-issued then — there is no token to request from anyone right now.

```bash
export CATHEDRAL_BEARER=$(openssl rand -hex 32)
```

### 4. Configure

Copy `config/testnet.toml` or `config/mainnet.toml` and fill in:

- `network.validator_hotkey` — your hotkey ss58.
- `network.wallet_name` — local Bittensor wallet name (default `default`).

The bearer env-var name and Polaris key hex are read from env, not config, so no further edits are required.

### 5. Bring up

```bash
cathedral-validator migrate --config config/testnet.toml
cathedral chain-check       --config config/testnet.toml  # confirm hotkey + subtensor
cathedral-validator serve   --config config/testnet.toml
```

Operational follow-on (systemd unit, log filtering, weight-status table, recovery): [validator/RUNBOOK.md](validator/RUNBOOK.md).

## FAQ

### Do I need any port open to the public internet?

No. Cathedral never connects back to your validator. The validator binds an HTTP admin/health server on `0.0.0.0:9333` by default (configurable via `http.listen_host` / `http.listen_port`) but that's for your own local ops tooling. Bind it to `127.0.0.1` or firewall it from the public internet. Outbound HTTPS 443 to `api.cathedral.computer` and your subtensor endpoint is the only network requirement.

### Do I need a GPU?

No. The validator does no model inference. A small CPU-only box is the recommended deployment.

### Where do I get `CATHEDRAL_BEARER`? Who do I send it to?

You generate it yourself, locally:

```bash
export CATHEDRAL_BEARER=$(openssl rand -hex 32)
```

You don't send it to anyone. The publisher doesn't enforce authentication on `/v1/leaderboard/recent` today — the validator binary just needs the env var set to a non-empty string so the `Authorization` header builds without crashing. Server-side enforcement and per-validator tokens are a later release; tokens will be re-issued then.

### Where do I get the Cathedral and Polaris public keys?

From the JWKS endpoint, served by the publisher:

```bash
curl -s https://api.cathedral.computer/.well-known/cathedral-jwks.json
```

Use `kid: cathedral-eval-signing` for `CATHEDRAL_PUBLIC_KEY_HEX`. The Polaris key is published in the same document as `kid: polaris-runtime-attestation`. Both are 64-character lowercase hex strings — exactly 64 chars, no leading-zero padding, no whitespace, no quotes. If your copy is a different length, recopy from the URL above; Ed25519 pubkeys cannot be padded.

### What if my pubkey is the wrong length?

You miscopied. Ed25519 pubkeys are 32 random bytes encoded as 64 lowercase hex characters. They cannot be padded with zeros to reach 64 chars — left-padding produces a different point on the curve and every signature verification will silently fail. Re-fetch from the JWKS endpoint above.

### What does the validator actually do?

Polls `https://api.cathedral.computer/v1/leaderboard/recent` every 12s, fetches signed `EvalRun` projections produced by the publisher, verifies the Ed25519 Cathedral signature locally, persists scored evals to a local SQLite store, and sets weights on chain. That's it. No miner connections, no model inference, no decrypting bundles.

### Why doesn't the validator need to verify Polaris attestations directly?

The publisher verifies the Polaris attestation before it signs the `EvalRun` projection. The Cathedral signature you verify locally is the publisher's attestation that *it* verified Polaris. You verify one signature per eval, the publisher's, against the key you pinned in step 2 of the Quickstart.

### How much disk will I grow into?

Scored evals are compact rows (a few KB each). At current throughput (~20 evals/day across all cards), expect a few hundred MB over the first six months. The 50 GB SSD recommendation is mostly headroom for systemd journal, OS, and Bittensor wallet — the validator's own state is small.

### Can I run multiple validators from the same host?

Yes — different `[network]` sections in separate config files, different `listen_port`, different `database_path`. Bittensor validator-binding is per-hotkey, not per-host, so each instance needs its own hotkey registered on the relevant subnet.

## Future enhancements

Listed in the order most likely to ship:

- **Validator pull-loop in production.** `cathedral.validator.pull_loop` already verifies signatures and upserts to the local `scores` table; the live binary just needs to point at `https://api.cathedral.computer/v1/leaderboard/recent` with a `since` cursor.
- **On-chain weekly Merkle anchoring.** `cathedral.publisher.merkle.epoch_for` and `cathedral.chain.anchor` exist; the missing piece is a scheduler that calls `system.remarkWithEvent` once per epoch with the Merkle root over the epoch's `EvalRun`s.
- **TEE attestation verifiers wired live.** `cathedral.attestation.nitro` is implemented; TDX and SEV-SNP return 501 from the submit endpoint. The contract is in [ATTESTATION_CONTRACT.md](ATTESTATION_CONTRACT.md).
- **JWKS endpoint.** `GET /.well-known/cathedral-jwks.json` on the publisher so validators don't need an out-of-band key handoff.
- **Layer-2 audit replay.** Validators randomly sample a bundle, re-run the miner's `soul.md` on a different task, and compare structural similarity (citation overlap, source-class profile, summary length) against the submitted card. Catches a miner who passes attestation by running an approved runtime but feeds it cherry-picked sources.
- **Per-domain rubric profiles.** Different weights for legal vs. finance vs. science cards. The publisher already carries `scoring_rubric` per `card_definition`; the in-process registry needs to read from there instead of `ScoreParts.weighted`.
- **Approved-runtime registry on-chain.** Currently a git JSON file (see [ATTESTATION_CONTRACT.md](ATTESTATION_CONTRACT.md) §6.2); will move to a multi-sig-controlled chain extrinsic when the Hermes release cadence stabilizes.

## Known limitations

These are the rough edges a validator operator should understand before depending on the system.

- **Single publisher.** Today there is one Cathedral publisher (`api.cathedral.computer`). Validators verify its signature, but the publisher is a centralization point for intake, eval orchestration, and signing. Distributed publishers are post-v1.
- **LLM provider trust.** The runtime calls Chutes (or any HTTPS LLM endpoint configured via `CHUTES_BASE_URL`). Polaris attests that "this measured runtime image, given this bundle, emitted this output bytestream." Polaris cannot attest "DeepSeek actually returned this token sequence." A compromised LLM provider can poison every Tier A submission on its model.
- **Citation re-fetch is best-effort.** The runtime fetches every cited URL and the publisher accepts statuses in `[200, 400)`. Regulator sites can rate-limit, redirect, or return `202 + empty body`. Anything outside that range fails preflight; some legitimate sources will fail intermittently. Miners learn to pin official mirrors.
- **Hotkey-keyed history.** Score history is keyed by `miner_hotkey` ss58. Losing the hotkey means losing the history; there is no recovery.
- **Marketplace TTL.** The Polaris marketplace eval has a 30-day TTL (env var `POLARIS_MARKETPLACE_EVAL_TTL_MINUTES` configurable). After expiry, a forced re-eval reprovisions the runtime container, charged to the operator's Verda balance. A small known cost line.
- **`polaris-v1` does not directly sign `bundle_hash`.** The Tier A attestation binds `output_hash`, `task_hash`, `submission_id`, and `deployment_id`; the bundle-to-submission binding is via Polaris's internal marketplace record (cross-referenced at verification time). A future `polaris-v2` will sign `bundle_hash` directly. Documented in [ATTESTATION_CONTRACT.md](ATTESTATION_CONTRACT.md) §9.1.
- **No live TEE miners.** Nitro verifier is wired; TDX and SEV-SNP return 501. Tier B+ is spec-only today.
- **On-chain weights from the new signature stream are not yet running.** The legacy `/v1/claim` weight loop still drives chain weights. The new producer pipeline ships signed projections; the validator just isn't pulling them into the live weight set yet.
- **Served projection includes fields outside the signed payload.** The publisher signs over nine fields (`id, agent_id, agent_display_name, card_id, output_card, output_card_hash, weighted_score, polaris_verified, ran_at`; see `src/cathedral/eval/scoring_pipeline.py::score_and_sign`). The `_eval_run_to_output` projection served at `/v1/leaderboard/recent` and `/v1/agents/{id}` additionally carries `polaris_attestation`, `cathedral_signature`, and `merkle_epoch`. `cathedral_signature` and `merkle_epoch` are excluded from the canonical bytes (`cathedral.v1_types.canonical_json`), so they are safe; `polaris_attestation` is not, and a naive pass of the served dict through `verify_eval_output_signature` will fail. A validator must strip `polaris_attestation` (and any other non-signed fields) before canonicalizing. The eval-verification walkthrough below does this explicitly. Tracked for the next ship: either move `polaris_attestation` into the signed payload, or extend the excluded-keys set so the served and signed dicts canonicalize to the same bytes.

## How to verify a specific eval

Pick any agent from the live leaderboard (e.g. `https://cathedral.computer/cards/eu-ai-act/`). Grab its `agent_id` and run:

```bash
curl -s https://api.cathedral.computer/v1/agents/{agent_id} | jq '.recent_evals[0]' > eval.json
```

The object has the public EvalOutput projection plus `cathedral_signature` and an embedded `polaris_attestation` (when Tier A). Verify in this order. Each step is a yes/no; if any fails, the eval is invalid.

**1. Cathedral signature.**

Build the canonical bytes from the nine signed fields and verify with the Cathedral public key. The served projection carries extras (`polaris_attestation`, `cathedral_signature`, `merkle_epoch`) that are not part of the signed payload; strip them before canonicalizing.

```python
import base64, json
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

eval_run = json.load(open("eval.json"))
SIGNED_KEYS = {
    "id", "agent_id", "agent_display_name", "card_id",
    "output_card", "output_card_hash",
    "weighted_score", "polaris_verified", "ran_at",
}
payload_dict = {k: eval_run[k] for k in SIGNED_KEYS if k in eval_run}
canonical = json.dumps(payload_dict, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")

pk = Ed25519PublicKey.from_public_bytes(bytes.fromhex(CATHEDRAL_PUBLIC_KEY_HEX))
sig = base64.b64decode(eval_run["cathedral_signature"])
pk.verify(sig, canonical)   # raises InvalidSignature if bad
```

Reference for the publisher-side signing payload: `cathedral.eval.scoring_pipeline.score_and_sign` (the `public_payload` dict). See **Known limitations** for the served-vs-signed mismatch the explicit allow-list above works around.

**2. Polaris attestation.**

If the eval row carries `polaris_attestation`, verify its signature with the pinned Polaris attestation public key:

```python
attestation = eval_run["polaris_attestation"]
payload = json.dumps(attestation["payload"], sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
polaris_pk = Ed25519PublicKey.from_public_bytes(bytes.fromhex(POLARIS_ATTESTATION_PUBLIC_KEY))
polaris_pk.verify(base64.b64decode(attestation["signature"]), payload)
```

Reference: `cathedral.eval.polaris_runner.PolarisRuntimeRunner._verify_attestation`.

**3. Output hash.**

Recompute `BLAKE3(canonical_json(output_card))` and confirm it matches `output_card_hash`:

```python
import blake3
canonical_card = json.dumps(eval_run["output_card"], sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
assert blake3.blake3(canonical_card).hexdigest() == eval_run["output_card_hash"]
```

If the eval has a Polaris attestation, also confirm `output_card_hash` against `polaris_attestation.payload.output_hash`. Note: the Polaris-side `output_hash` is over the base64-decoded bytes the runtime returned; for Tier A evals this is the same canonical-JSON bytes since the runtime emits a canonical card.

**4. Citations re-fetch.**

For each entry in `output_card.citations`, GET the URL, compute `BLAKE3` of the response body, and compare to `citation.content_hash`. Confirm the HTTP status is in `[200, 400)`. Reference implementation lives in the runtime: `docker/cathedral-runtime/server.py`.

External regulator sites can rate-limit or change between miner fetch and your re-fetch. A mismatch here is a soft signal, not a definitive forgery proof; but a citation that returns 404 today and 200 at miner-fetch time is a yellow flag worth investigating.

**5. Score reproduction.**

Re-run the six-dimension scorer against the `output_card` and the registry entry for `card_id`:

```python
from cathedral.cards.score import score_card
from cathedral.cards.registry import CardRegistry
from cathedral.types import Card

card = Card.model_validate(eval_run["output_card"])
entry = CardRegistry.baseline().lookup(eval_run["card_id"])
parts = score_card(card, entry)
recomputed = parts.weighted()
# Apply first-mover delta + 1.10x verified multiplier as in src/cathedral/eval/scoring_pipeline.py
# to reproduce eval_run["weighted_score"], capped at 1.0.
```

The first-mover delta requires the submission's `metadata_fingerprint` and the historical incumbent score, which need a DB join; the formulas are in `cathedral.eval.scoring`.

**If any step fails, the eval is invalid.** Steps 1 and 2 cover cryptographic integrity. Step 3 confirms the served card matches the hashed projection. Step 4 confirms citation provenance. Step 5 confirms the publisher applied the rubric honestly.
