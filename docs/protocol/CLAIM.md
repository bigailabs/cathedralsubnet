# Wire format: `cathedral.polaris_agent_claim.v1`

Implements issue #77. A miner submits this JSON to the validator's `/v1/claim` endpoint.

## Schema

```jsonc
{
  "type": "cathedral.polaris_agent_claim.v1",
  "version": "v1",
  "miner_hotkey": "5F...",
  "owner_wallet": "5G...",
  "work_unit": "card:eu-ai-act",
  "polaris_agent_id": "agt_01H1234567890ABCDEF",
  "polaris_deployment_id": "dep_01H...",       // optional
  "polaris_run_ids":      ["run_01H...", "..."], // optional
  "polaris_artifact_ids": ["art_01H...", "..."], // optional
  "submitted_at": "2026-05-10T18:00:00Z"
}
```

## Required fields

- `miner_hotkey` — the hotkey signing the claim
- `owner_wallet` — coldkey on file with Polaris; used to detect self-loop usage
- `work_unit` — opaque identifier the validator uses for de-dup and rate limits
- `polaris_agent_id` — the agent record the validator pulls

## Validator behavior

1. Shape validation (`cathedral_types::PolarisAgentClaim::validate_shape`)
2. Pull manifest from Polaris by `polaris_agent_id`
3. Verify manifest Ed25519 signature against the configured public key
4. Reject if `manifest.polaris_agent_id != claim.polaris_agent_id`
5. Pull and verify each run, artifact, and usage record by ID
6. Drop signature-failing run/artifact/usage records (record count for observability)
7. Filter usage by class (external only) and refunded/flagged flags
8. Hand the resulting `EvidenceBundle` to the card scorer

## Failure modes

| Failure | Validator response |
|---|---|
| Missing required field | HTTP 400 |
| Manifest fetch 404 | Reject claim (logged) |
| Manifest signature invalid | Reject claim (logged) |
| Manifest agent_id mismatch | Reject claim (logged) |
| Run/artifact/usage signature invalid | Drop that record only |
| Artifact bytes hash mismatch | Drop that record only |

## Self-loop detection

The current implementation checks `owner_wallet == consumer_wallet`. The Polaris usage schema must include `consumer_wallet` for this filter to function. See `cathedral-evidence::filter::is_self_loop`.
