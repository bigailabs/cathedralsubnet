# Scoring

Implements issue #3. The card scorer is in `cathedral.cards.score`.

## Six dimensions

Each in `[0.0, 1.0]`:

| Dimension | What it measures | Default weight |
|---|---|---|
| `source_quality` | Share of citations from official classes; required-class coverage bonus | 0.30 |
| `maintenance` | Time since last refresh vs cadence | 0.20 |
| `freshness` | Continuous decay from cadence to 4× cadence | 0.15 |
| `specificity` | Length of `what_changed` + `why_it_matters` | 0.15 |
| `usefulness` | Action notes + risks + confidence | 0.10 |
| `clarity` | Summary length and sentence count | 0.10 |

Sum of weights = 1.0. Tunable via `cathedral.types.ScoreParts.weighted` (subclass to override).

## Official source classes

`government`, `regulator`, `court`, `parliament`, `law_text`, `official_journal`.

A card whose citations are 100% official scores `1.0` on the base; non-official citations dilute it linearly. Required-class coverage adds up to `+0.20` when the card matches the registry entry's required classes.

## Freshness curve

```
ratio = age_hours / cadence_hours
ratio <= 1.0  ->  1.0
ratio >= 4.0  ->  0.0
otherwise     ->  1.0 - (ratio - 1.0) / 3.0
```

## Maintenance bands

| Age | Score |
|---|---|
| `<= cadence` | 1.0 |
| `<= 2× cadence` | 0.6 |
| `<= 4× cadence` | 0.2 |
| `> 4× cadence` | 0.0 |

## Preflight (failure modes)

Cards fail before scoring if:

- No citations
- Any citation HTTP status outside `200..400`
- Missing `no_legal_advice` marker
- Empty `summary`, `what_changed`, or `why_it_matters`
- Legal-advice framing (`you should`, `we recommend`, `as your lawyer`, etc.)

A failed card receives no score; the claim is rejected with `preflight: <reason>`.

## Baseline registry

`CardRegistry.baseline()` seeds five cards: `eu-ai-act`, `us-ai-executive-order`, `uk-aisi`, `eu-gdpr-enforcement`, `us-ccpa`. Operators can override via TOML in a future config field; for now, edit `cathedral.cards.registry` directly.

## Verified-runtime multiplier (Tier A, gated)

v1 ships the verified-runtime multiplier at `1.00x`. The `1.10x` multiplier described in this section applies only when Tier A is enabled by setting `CATHEDRAL_ENABLE_POLARIS_DEPLOY=true`; with the flag off (the v1 default) every scored run uses `1.00x`. Code reference: `src/cathedral/eval/scoring_pipeline.py` (~line 264).

When Tier A is enabled and an eval run produces a valid Polaris attestation, the `1.10x` quality multiplier is applied AFTER the first-mover delta, then capped at `1.0`. The multiplier reflects that the work ran inside a Cathedral-managed runtime image on Polaris compute, not on the miner's own hardware.

### Eligibility tiers

| Tier | Runner | Polaris-verified | Multiplier | Live in v1 |
|---|---|---|---|---|
| B, BYO-compute (ssh-probe) | `SshProbeRunner` / `SshHermesRunner` | no | 1.00x | yes |
| B, BYO-compute (bundle) | `BundleCardRunner` | no | 1.00x | yes |
| A, Polaris-hosted | `PolarisRuntimeRunner` | yes (signed attestation verifies) | 1.10x when gate is on; 1.00x otherwise | no, gated behind `CATHEDRAL_ENABLE_POLARIS_DEPLOY=true` |
| Legacy | `HttpPolarisRunner`, stubs | yes when `polaris_agent_id` is non-empty | 1.10x when gate is on; 1.00x otherwise | no, gated |

### Attestation format

Tier A runs persist the full attestation alongside the eval row in `eval_runs.polaris_attestation` (JSON). The structure is pinned by the Polaris `runtime-evaluate` contract:

```jsonc
{
  "version": "polaris-v1",
  "payload": {
    "submission_id": "sub_cathedral_runtime_v1",
    "task_id":       "cathedral-eu-ai-act-e42r3",
    "task_hash":     "<blake3(task_prompt_utf8) hex>",
    "output_hash":   "<blake3(output_bytes) hex>",
    "deployment_id": "dep_abc123",
    "completed_at":  "2026-05-10T10:01:23.456Z"
  },
  "signature":  "<base64 Ed25519 over canonical_json(payload)>",
  "public_key": "<hex Ed25519 public key, must equal POLARIS_ATTESTATION_PUBLIC_KEY>"
}
```

### Verification (runner-side, every eval)

`PolarisRuntimeRunner` performs the following checks before returning a Card; any failure raises `PolarisAttestationError` and the orchestrator marks the run as a runner failure (no score persisted):

1. Recompute `task_hash = blake3(task.prompt.encode("utf-8"))` and confirm it matches `payload.task_hash`.
2. Recompute `output_hash = blake3(output_bytes)` where `output_bytes = base64-decode(response.output)`. Confirm it matches `payload.output_hash`.
3. Confirm `payload.task_id` equals the id Cathedral sent (`cathedral-{card_id}-e{epoch}r{round_index}`).
4. Confirm `payload.submission_id` equals the configured `POLARIS_CATHEDRAL_RUNTIME_SUBMISSION_ID`.
5. Confirm the response's `public_key` equals the configured `POLARIS_ATTESTATION_PUBLIC_KEY` (pinning — no key rotation via response).
6. Ed25519-verify `signature` over `canonical_json(payload)` (sorted keys, no whitespace, UTF-8) using the pinned public key.

### Re-verification (validators, audit)

Validators and the cathedral.computer frontend can re-verify any attestation offline by reading `eval_runs.polaris_attestation` and re-running steps 3–6 above. Steps 1–2 require the original task prompt and output bytes, both of which are persisted (`task_json` and `output_card_json`).

The public `EvalOutput` projection exposes `polaris_attestation` alongside `polaris_verified` and `cathedral_signature` so external auditors don't need DB access to replay the verification.

### v1 known weakness — bundle decryption key handoff

Cathedral currently ships the bundle encryption key (KEK) to the Polaris runtime via `env_overrides.CATHEDRAL_BUNDLE_KEK`. A future revision will move to per-bundle data-key wrapping with a Polaris-side KMS, eliminating Cathedral's exposure window on the long-lived KEK.
