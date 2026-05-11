# Cathedral v1 Attestation Contract

Status: v1 draft, 2026-05-10.
Scope: defines how miners prove that the agent named in a Cathedral submission produced the output card the submission carries.
Audience: Cathedral validator implementers, Polaris attestation service, miners building B+ (self-TEE) compute paths, downstream auditors.
Companion documents: `cathedral-redesign/CONTRACTS.md` (data contracts), `docs/protocol/CLAIM.md` (legacy `/v1/claim`), `docs/protocol/SCORING.md` (card scoring).

> This spec is the layer that decides which submissions earn TAO emissions and rank on the leaderboard, and which submissions live only on the discovery surface. It does not define how cards are scored. It does not define on-chain weight setting. It defines the *trust statement* that gates entry to those flows.

---

## 1. Purpose & non-goals

### 1.1 Purpose

Cathedral is a regulatory-intelligence subnet. Miners submit Hermes-compatible bundles that consume a card task, produce a card JSON, and ship the resulting artifact to the Cathedral publisher. The publisher must answer one question before that submission can earn emissions or rank on the leaderboard: *did the bundle named in this submission actually produce this output?*

There are three operationally distinct ways to answer that question, each with a different trust root, and this document specifies the wire format, verification procedure, and threat model for each. The three paths are not interchangeable. A submission verified by Polaris is not "as good as" a submission verified by AWS Nitro; they protect against different failure modes and ride different revocation calendars. The validator must track which path each submission used and which root of trust applied.

The document is written as an RFC. It is normative for any validator that emits Cathedral weights and for any miner that wants their submission to count for emissions. The HTTP endpoint behavior in Section 8 is enforced by the publisher; everything else is enforced by the validator's worker loop and the leaderboard publisher.

### 1.2 Non-goals

This document does NOT cover:

- Card scoring. See `docs/protocol/SCORING.md`.
- Bundle format, Hermes runtime, or eval orchestration. See `cathedral-redesign/CONTRACTS.md` §6.
- On-chain weight setting or Merkle anchoring. See `CONTRACTS.md` §4.5 and §4.6.
- zkML or SNARK-based proof of inference. Noted in Section 11 as v2 future work.
- Cross-tier challenge governance (one miner disputing another's verified tier). Noted in Section 11.
- Polaris's internal signing infrastructure. Treated as an external dependency; this document references the Polaris-side spec where relevant.

### 1.3 What "attestation" means here

In this spec, an **attestation** is a signed structured statement that binds the following four facts together as a single cryptographic claim:

1. The bundle the miner submitted (`bundle_hash`).
2. The task Cathedral specified (`task_hash`).
3. The output card the bundle produced (`output_hash`).
4. The identity of the miner (`miner_hotkey`).

A signature alone over the output is not an attestation. A bundle hash alone is not an attestation. The combined binding is what lets the validator say "this hotkey caused this bundle to produce this output for this task." Each tier produces this binding from a different trust root.

---

## 2. Three tiers overview

| Tier | Verified? | Earns TAO emissions? | Leaderboard rank? | Discovery surface? |
|------|-----------|----------------------|-------------------|--------------------|
| **A: Polaris-hosted** | Yes (Polaris Ed25519 attestation) | Yes | Yes | Yes |
| **B+: Self-TEE** | Yes (hardware attestation: AWS Nitro / Intel TDX / AMD SEV-SNP) | Yes | Yes | Yes |
| **B: Unverified** | No | NO | NO | Yes (browsable + purchasable, never ranked) |

### 2.1 Verified vs discovery: the surface split

The leaderboard is verified-only. A submission must produce a Tier A or Tier B+ attestation that validates under the rules in Sections 4 and 5 to receive a weight in the next epoch's weight set. Anything else lands in the discovery / research marketplace surface, which is a separate product. The discovery surface is browsable, search-indexed, and the bundles can be purchased per-card or per-bundle, but they are never ranked against verified submissions and never earn TAO emissions.

This is a deliberate product split, not a soft filter. The publisher persists every submission with an `attestation_tier` column (`A`, `B+`, or `B`). The weight loop reads only rows where `attestation_tier IN ('A', 'B+')`. The leaderboard API filters the same way. The discovery API reads the full table.

### 2.2 Why three tiers and not two

The honest reason for three tiers is that the eligible Cathedral mining population is broader than either "trusts Polaris" or "owns a TEE-capable bare-metal host." Tier A is the easy on-ramp for miners who run Hermes inside Polaris's managed environment. Tier B+ is for miners who want sovereignty over their compute but can produce hardware attestations. Tier B exists because *some* of the most valuable regulatory intelligence will come from researchers and analysts who do not run agents at all; they assemble cards by hand or with home-grown tooling, and the only useful product surface for them is discovery.

Collapsing Tier B into "rejected" would lose this material. Collapsing Tier B+ into Tier A would force everyone through Polaris, which is a single point of failure and a business hostage. The split is durable. It is encoded in the database, the API, the validator weight loop, and the on-chain anchor.

### 2.3 Tier promotion and demotion

A submission's `attestation_tier` is fixed at submission time. There is no in-place promotion (e.g. a Tier B miner cannot later "upgrade" the same submission to Tier A by attaching a Polaris attestation after the fact). To change tiers, the miner re-submits the bundle through the appropriate endpoint and receives a new submission id. The original submission remains on the discovery surface unless the miner explicitly retires it.

This rule is non-negotiable. Allowing post-hoc tier promotion would require the publisher to recompute the first-mover anchor (CONTRACTS.md §7.2), the Merkle root (§4.5), and the leaderboard ranks across epochs that have already been published and anchored on-chain. The cost is not worth the convenience.

---

## 3. Common attestation envelope

Every attestation, regardless of tier, MUST carry the seven core fields below. The publisher rejects any attestation missing or malformed at the field level before the tier-specific signature verification runs. Fields are listed in their canonical JSON sort order.

| Field | Type | Description |
|-------|------|-------------|
| `attestation_version` | string | Per-tier version tag (e.g. `polaris-v1`, `nitro-v1`, `tdx-v1`, `sevsnp-v1`). |
| `bundle_hash` | string | BLAKE3 lowercase hex (64 chars) of the plaintext bundle zip bytes. |
| `card_id` | string | The Cathedral `card_definitions.id` the bundle was run against. |
| `miner_hotkey` | string | Bittensor sr25519 ss58 address. Must equal the `X-Cathedral-Hotkey` header on submission. |
| `output_hash` | string | BLAKE3 lowercase hex (64 chars) of the canonical-JSON serialized card the bundle produced. |
| `task_hash` | string | BLAKE3 lowercase hex (64 chars) of the canonical-JSON serialized task bytes Cathedral specified. |
| `timestamp` | string | ISO-8601 UTC, millisecond precision, mandatory `Z` suffix. e.g. `2026-05-10T18:00:00.123Z`. |

The envelope is wrapped in a tier-specific outer structure (see Sections 4 and 5). The verification flow is always:

```
                                                +-------------------+
   miner submits ------>  publisher computes    | bundle_hash       |
   bundle + attestation   bundle_hash, output_  | output_hash       |
                          hash, looks up task_  | task_hash (Cath)  |
                          hash by card_id       | miner_hotkey      |
                                                +---------+---------+
                                                          |
                                                          v
                              +-------------------------------------------+
                              | check attestation envelope fields match   |
                              | the server-computed values byte-for-byte  |
                              +-------------------------+-----------------+
                                                        |
                                                        v
                              +-------------------------------------------+
                              | dispatch on attestation_version:          |
                              |   polaris-v1  -> Section 4                |
                              |   nitro-v1    -> Section 5.1              |
                              |   tdx-v1      -> Section 5.2              |
                              |   sevsnp-v1   -> Section 5.3              |
                              | anything else -> 422 unsupported_version  |
                              +-------------------------+-----------------+
                                                        |
                                                        v
                              +-------------------------------------------+
                              | tier-specific signature + measurement     |
                              | check; on pass -> attestation_tier        |
                              | set; on fail -> 422 with reason           |
                              +-------------------------------------------+
```

Note that the publisher does NOT trust the field values inside the attestation envelope for `bundle_hash`, `output_hash`, or `miner_hotkey`. It computes those server-side and requires the attestation to MATCH them. The signature verification then happens over the locked, server-trusted bytes: the attestation is only valid if the signer signed the same statement the server already knows to be true. This forecloses the entire family of attacks where a miner submits a different bundle than the one they attested to.

`task_hash` is server-derived. The publisher computes the canonical hash of the current `card_id` task spec at submission time and rejects any attestation whose `task_hash` does not match. This means an attestation generated against an outdated task spec is invalid even if it verifies cryptographically. Task specs evolve; see Section 6 on the approved runtime registry for how versioning interacts.

### 3.1 Submission carriage

Tier-A and Tier-B+ submissions go to `POST /v1/agents/submit` (CONTRACTS.md §2.1) and attach an additional multipart form field `attestation` containing the attestation JSON as a UTF-8 string. The form field is required for any submission that wants `attestation_tier != 'B'`. If `attestation` is absent or empty, the submission is automatically classified as Tier B (unverified) and routed to the discovery surface. The submission still completes; it just does not earn emissions.

The existing `X-Cathedral-Signature` header continues to carry the miner's sr25519 hotkey signature over the canonical submission payload (CONTRACTS.md §4.1). The attestation is a *separate* signed object, signed by a different party (Polaris or a hardware vendor), and bound to the miner's hotkey through the `miner_hotkey` field inside the envelope. Both signatures must verify or the submission is downgraded to Tier B.

---

## 4. Tier A: Polaris attestation

### 4.1 Format

Polaris runs the miner-supplied Hermes bundle on Polaris compute. After the run terminates, Polaris's attestation service produces the following structure and returns it to the miner alongside the produced card JSON:

```json
{
  "version": "polaris-v1",
  "payload": {
    "submission_id": "sub_01JABCDXYZ...",
    "task_id": "task_eu-ai-act_2026w19",
    "task_hash": "<blake3 lowercase hex of task bytes>",
    "output_hash": "<blake3 lowercase hex of output bytes>",
    "deployment_id": "dep_01H7XJ2K8N3Y0M6S9P4QV",
    "completed_at": "2026-05-10T18:00:00.123Z"
  },
  "signature": "<base64 Ed25519 over canonical_json(payload)>",
  "public_key": "<hex Ed25519, 64 chars>"
}
```

The Polaris attestation is signed using the same canonicalization rules as the rest of the Polaris-Cathedral signing contract (CONTRACTS.md §4.3): `json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)`, no whitespace, UTF-8 bytes. The `signature` field is dropped before signing. Polaris's signing key is the same Ed25519 key already in production for manifest/run/artifact records (`POLARIS_CATHEDRAL_SIGNING_KEY`), published at `GET /.well-known/polaris/cathedral-jwks.json`.

### 4.2 Cathedral-side envelope mapping

The publisher transforms the Polaris-native structure above into the common envelope from Section 3 by adding three Cathedral-side fields and persisting both. The mapping is:

| Common envelope | Source |
|-----------------|--------|
| `attestation_version` | `"polaris-v1"` (literal) |
| `bundle_hash` | Server-computed from uploaded zip bytes; must match what Polaris ran. |
| `card_id` | Form field on the submission; must match Polaris `task_id` after Cathedral's task-id-to-card-id mapping (see 4.4). |
| `miner_hotkey` | `X-Cathedral-Hotkey`; bound to the Polaris `submission_id` via the marketplace record. |
| `output_hash` | From `payload.output_hash`; server recomputes from the produced card and requires byte-equality. |
| `task_hash` | From `payload.task_hash`; server recomputes from current `card_definitions[card_id].task_spec` and requires equality. |
| `timestamp` | From `payload.completed_at`. |

The Polaris-native fields (`submission_id`, `deployment_id`) are preserved in the persisted attestation record but are not part of the common envelope.

### 4.3 Verification steps

The publisher performs the following steps, in order, and short-circuits on the first failure:

1. **Shape check.** Parse the attestation as JSON. Require keys `version`, `payload`, `signature`, `public_key`. Require `version == "polaris-v1"`. Require `payload` to be a JSON object containing the six fields above. Anything else → 422 `attestation_shape`.
2. **Public-key pinning.** Compare `public_key` to the configured Polaris attestation key (`POLARIS_ATTESTATION_PUBLIC_KEY` env var, hex Ed25519, 64 chars). If they differ AND the supplied key is not in the historical key list (see 4.5), reject. → 422 `attestation_unknown_signer`.
3. **Signature verification.** Build `canonical_json(payload)` per the rule above. Verify the base64-decoded `signature` against the pinned (or historically valid) Polaris public key. → 422 `attestation_signature_invalid` on failure.
4. **Envelope binding.** Compare `payload.task_hash`, `payload.output_hash`, `payload.completed_at` against server-computed values. The output_hash check is the load-bearing one: the publisher computes BLAKE3 of the canonical-JSON serialization of the output card and requires byte-equality. → 422 `attestation_binding_mismatch` (with field name) on failure.
5. **Polaris cross-reference.** Look up `payload.submission_id` against Polaris's marketplace record. The miner's claimed hotkey MUST equal the marketplace record's `worker_hotkey`. The deployment id must match. → 422 `attestation_marketplace_mismatch` on failure. (In v1, this step uses the existing Polaris contract endpoints; see CONTRACTS.md §2.13.)
6. **Freshness.** Reject if `payload.completed_at` is more than 48 hours older than server clock, or more than 5 minutes in the future. → 422 `attestation_stale` or `attestation_future`.

On all six steps passing: persist the attestation, set `attestation_tier = 'A'`, proceed to similarity check and bundle storage as normal.

### 4.4 Task-id-to-card-id mapping

Polaris speaks `task_id`; Cathedral speaks `card_id`. The mapping is one-to-one and lives in `card_definitions.polaris_task_id` (added to the schema as part of this contract). The publisher resolves the Polaris `task_id` to a Cathedral `card_id` and requires the submission's form field `card_id` to match. If the Polaris task is unknown to Cathedral, the submission is downgraded to Tier B with reason `attestation_unknown_task`; the bundle and card are still preserved on the discovery surface.

### 4.5 Polaris key rotation

The pinned `POLARIS_ATTESTATION_PUBLIC_KEY` is the *currently active* Polaris attestation signing key. To support rotation without downtime, the validator also reads `POLARIS_ATTESTATION_PUBLIC_KEYS_HISTORICAL` (comma-separated hex keys, optional) and accepts signatures from any key on the combined list. Rotation procedure:

1. Polaris generates a new key, publishes it at the JWKS endpoint, and notifies Cathedral operators (out of band).
2. Cathedral operators append the new key to `POLARIS_ATTESTATION_PUBLIC_KEYS_HISTORICAL`, keep the old key in `POLARIS_ATTESTATION_PUBLIC_KEY` for one publication epoch (one week), then promote the new key to `POLARIS_ATTESTATION_PUBLIC_KEY` and move the old key into the historical list.
3. The old key remains in the historical list for at least 90 days to allow late-arriving submissions referencing recent runs to verify.

The historical list is bounded at five keys. Older keys are removed by operations, not by code. There is no on-chain key registry in v1; the env var is the source of truth. v2 will move this to a JWKS endpoint that the validator pulls on a heartbeat.

---

## 5. Tier B+: TEE attestations

Tier B+ accepts hardware-vendor attestations from three platforms. The structural pattern is the same in all three cases: the vendor's attestation document contains (a) a measurement of the runtime image, (b) a vendor signature chain back to the vendor's published root certificate, and (c) a "user data" or "report data" field into which the runtime binds a hash of the workload-specific data (here: the common envelope from Section 3). Cathedral verifies the signature chain, checks the measurement against the approved runtime registry (Section 6), and confirms the user-data binding.

The three subsections below state what Cathedral validates. They do NOT restate the full attestation document format; that is documented by the vendors. Implementers MUST read the vendor docs cited in each subsection.

### 5.1 AWS Nitro Enclaves

**Wire format reference:** AWS Nitro Enclaves Attestation Document spec ([https://docs.aws.amazon.com/enclaves/latest/user/verify-root.html](https://docs.aws.amazon.com/enclaves/latest/user/verify-root.html)). CBOR-serialized COSE_Sign1 document. The miner submits the base64-encoded attestation document as the `attestation` form field. Cathedral wraps it in a thin Cathedral-side envelope:

```json
{
  "version": "nitro-v1",
  "attestation_doc_b64": "<base64 of CBOR COSE_Sign1>",
  "envelope": {
    "attestation_version": "nitro-v1",
    "bundle_hash": "<...>",
    "card_id": "<...>",
    "miner_hotkey": "<...>",
    "output_hash": "<...>",
    "task_hash": "<...>",
    "timestamp": "<...>"
  }
}
```

The `envelope` field contains the seven common-envelope fields from Section 3. The hash of `canonical_json(envelope)` is what the enclave bound into the attestation's `user_data` field at attestation time.

**What Cathedral validates:**

1. Decode the CBOR COSE_Sign1 document. Extract `payload`, `protected_headers`, `signature`.
2. Verify the COSE_Sign1 signature using the certificate chain in the protected headers, terminating at the AWS Nitro root certificate. The AWS Nitro root is pinned in Cathedral config (`AWS_NITRO_ROOT_CERT_PEM` env var) and refreshed quarterly per AWS's publication schedule at `https://aws-nitro-enclaves.amazonaws.com/AWS_NitroEnclaves_Root-G1.zip`.
3. Confirm the `PCR0` measurement equals one of the approved Hermes runtime image hashes (Section 6). PCR0 covers the enclave image file. PCR8 (the IAM-role-signed PCR) is not required in v1 but is recorded for forensics.
4. Confirm `user_data == blake3(canonical_json(envelope))`. The miner sets this when launching the enclave with `--attestation-user-data`. The publisher recomputes the canonical bytes server-side.
5. Confirm `timestamp` from the attestation document (the enclave's wall-clock value) is within 48 hours of server clock and within 5 minutes of the future.
6. Confirm `nonce` from the attestation document equals the nonce Cathedral previously issued to this miner via `GET /v1/attestation/nonce` (see Section 8.4). Nonces are single-use and expire after 1 hour.

On all six steps passing: persist the attestation, set `attestation_tier = 'B+'`.

### 5.2 Intel TDX

**Wire format reference:** Intel TDX Quote ([https://download.01.org/intel-sgx/latest/dcap-latest/linux/docs/Intel\_TDX\_DCAP\_Quoting\_Library\_API.pdf](https://download.01.org/intel-sgx/latest/dcap-latest/linux/docs/Intel_TDX_DCAP_Quoting_Library_API.pdf)). A TDX quote is a binary blob signed by an Intel-issued attestation key whose certificate chain terminates at Intel's Provisioning Certification Service (PCS) root. Cathedral envelope:

```json
{
  "version": "tdx-v1",
  "quote_b64": "<base64 of TDX quote>",
  "collateral": {
    "tcb_info_json": "<base64 PCS TCB info>",
    "qe_identity_json": "<base64 QE identity>",
    "pck_cert_chain_pem": "<base64 PEM chain>"
  },
  "envelope": { /* same as Section 5.1 */ }
}
```

**What Cathedral validates:**

1. Verify the PCK certificate chain in `collateral.pck_cert_chain_pem` terminates at Intel's PCS root, which is pinned in Cathedral config (`INTEL_TDX_PCS_ROOT_CERT_PEM`).
2. Verify the TDX quote signature using the leaf PCK cert.
3. Use Intel's `tcb_info` + `qe_identity` JSON to confirm the platform's TCB level meets or exceeds Cathedral's minimum policy (`INTEL_TDX_MIN_TCB`, defaulting to `OutOfDate`-rejecting). Out-of-date TCB → reject.
4. Confirm `mr_td` (the TDX measurement of the runtime) equals one of the approved Hermes runtime image hashes (Section 6).
5. Confirm `report_data == blake3(canonical_json(envelope))`. `report_data` is 64 bytes; Cathedral uses the first 32 bytes for the envelope hash and treats bytes 32 through 63 as the issued nonce (see Section 8.4).
6. Confirm the nonce in bytes 32 through 63 of `report_data` matches a previously issued, unexpired nonce.
7. Confirm the quote's signing time (`tcb_date` from collateral) is within 48 hours of server clock, future-skew 5 minutes.

On all seven steps passing: `attestation_tier = 'B+'`.

### 5.3 AMD SEV-SNP

**Wire format reference:** AMD SEV-SNP Attestation Report spec ([https://www.amd.com/system/files/TechDocs/56860.pdf](https://www.amd.com/system/files/TechDocs/56860.pdf), Appendix A). A 1184-byte binary report signed by the VCEK (Versioned Chip Endorsement Key). The VCEK certificate chain terminates at AMD's SEV-SNP root (the ARK/ASK certificates). Cathedral envelope:

```json
{
  "version": "sevsnp-v1",
  "report_b64": "<base64 of 1184-byte attestation report>",
  "vcek_cert_pem": "<base64 PEM VCEK cert>",
  "envelope": { /* same as Section 5.1 */ }
}
```

**What Cathedral validates:**

1. Fetch (or cache) the AMD ARK and ASK certificates for the platform's processor family. Pinned roots at `AMD_SEVSNP_ARK_CERT_PEM` and `AMD_SEVSNP_ASK_CERT_PEM`.
2. Verify the VCEK certificate chain terminates at ARK/ASK.
3. Verify the attestation report's signature with the VCEK cert.
4. Confirm `measurement` (bytes 144 through 191 of the report) equals one of the approved Hermes runtime image hashes (Section 6).
5. Confirm `report_data` (bytes 80 through 143 of the report) equals `blake3(canonical_json(envelope))` in its first 32 bytes; bytes 32 through 63 carry the nonce.
6. Confirm `tcb_version.snp_fw` and `tcb_version.microcode` meet the platform minimums in `AMD_SEVSNP_MIN_TCB`.
7. Confirm the report is fresh per the issued-nonce check (Section 8.4).

On all seven steps passing: `attestation_tier = 'B+'`.

### 5.4 Common TEE concerns

All three subsections share three properties that Cathedral relies on:

- **User-data binding.** The TEE allows the running workload to bind arbitrary 32-or-64-byte data into the attestation document. Cathedral always uses this slot to carry the BLAKE3 of the canonical envelope JSON, plus the nonce. This is the field that turns a generic "this is a measured TDX VM" attestation into "this specific output was produced by this measured TDX VM for this specific Cathedral task."
- **Measurement match against the registry.** A signed attestation document from AWS Nitro is not sufficient on its own; it only proves an enclave ran. The PCR0 / MRTD / measurement field must match an entry in Cathedral's approved runtime image hash list (Section 6) or the attestation is rejected. This is what makes the TEE attest specifically to "running an approved Hermes runtime" and not "running anything I want."
- **Freshness via issued nonce + timestamp.** Replay protection requires both. The nonce closes replay of an older valid attestation; the timestamp closes the case where a valid nonce was issued but the report was generated outside the freshness window for some other reason (clock skew, slow CI, etc.).

The publisher MAY accept any one of the three TEE platforms; it MUST NOT accept attestations from unknown TEE platforms in v1. A `version` value outside the set `{nitro-v1, tdx-v1, sevsnp-v1, polaris-v1}` → 422 `attestation_unsupported_platform`.

---

## 6. Approved runtime registry

### 6.1 Format and storage

Cathedral maintains a list of approved Hermes container image hashes. Only attestations whose runtime measurement matches an entry on this list are accepted for Tier A or Tier B+.

The list lives as **a JSON file at `config/approved_runtimes.json` in this repo, version-controlled in git, loaded by the validator at startup, and reloadable on `SIGHUP`**. Shape:

```json
{
  "version": "1",
  "updated_at": "2026-05-10T00:00:00Z",
  "runtimes": [
    {
      "name": "hermes-v0.7.2",
      "image_digest": "sha256:<oci image digest>",
      "nitro_pcr0_hex": "<48-byte hex>",
      "tdx_mrtd_hex": "<48-byte hex>",
      "sevsnp_measurement_hex": "<48-byte hex>",
      "released_at": "2026-05-09T15:00:00Z",
      "deprecated_at": null,
      "removed_at": null,
      "release_notes_url": "https://github.com/bigailabs/hermes/releases/tag/v0.7.2"
    }
  ]
}
```

Each entry carries the platform-specific measurement values precomputed by the Hermes release pipeline. Cathedral does NOT recompute them at runtime; it looks them up by exact equality.

### 6.2 Why a JSON file in git, not on-chain

Three alternatives were considered:

1. **On-chain registry.** Anchored as Bittensor extrinsics on the Cathedral subnet, every list change is a transaction. Rejected for v1: too high friction for the cadence of Hermes releases (multiple per week during the early period), and the consensus model doesn't add meaningful security over a git-signed JSON file because the validator already pulls code updates from git.
2. **Frozen in CONTRACTS.md.** Considered briefly; rejected because CONTRACTS.md is meant to be byte-stable across releases and the runtime registry needs to evolve weekly during v1.
3. **Hosted JSON at api.cathedral.computer.** Rejected because it makes the validator's trust root mutable by whoever controls DNS for api.cathedral.computer. A git file is auditable, signed by the merger of the PR, and goes through code review.

The git file wins on auditability and friction at the v1 cadence. v2 may move this to a multi-sig-controlled chain extrinsic; that decision is deferred.

### 6.3 Inclusion criteria

A new entry is added to the registry by PR to this repo. The PR MUST include:

- The exact OCI image digest (sha256) for the Hermes release.
- The platform measurements (PCR0, MRTD, SEV-SNP measurement) reproduced from the Hermes release pipeline's CI logs, linked in the PR.
- A link to the release notes describing the changes since the last accepted runtime.
- Approval from at least one Cathedral operator other than the PR author.

Entries are NEVER edited in place. To deprecate a runtime, set `deprecated_at` to a future ISO-8601 timestamp; attestations measuring against a deprecated runtime continue to verify until `deprecated_at`, after which they are rejected with `attestation_deprecated_runtime`. To remove a runtime entirely, set `removed_at`; rejected with `attestation_removed_runtime` immediately. Deprecation gives miners a grace window; removal is for the case where a runtime is found to be compromised and must be invalidated immediately.

### 6.4 Coverage policy

The active set of runtimes at any time is the entries with `removed_at == null` and `deprecated_at` either null or in the future. Cathedral commits to keeping at least the latest two minor Hermes versions active at all times. Older minor versions are deprecated on a 30-day notice, then removed. Patch versions (e.g. v0.7.2 → v0.7.3) replace the previous patch on the same minor without a grace period.

---

## 7. Canonical bytes & hashing

### 7.1 Canonical JSON

All signing and hashing operations in this spec use the same canonicalization rule, which matches `cathedral.types.canonical_json_for_signing` (and Polaris's `polaris.services.cathedral_signing.canonical_json_for_signing`):

```python
import json

def canonical_json(payload: dict) -> bytes:
    """Drop signature keys; sort keys; no whitespace; UTF-8."""
    EXCLUDED = {"signature", "cathedral_signature", "merkle_epoch"}
    body = {k: v for k, v in payload.items() if k not in EXCLUDED}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
```

Rules in detail:

- `sort_keys=True` ensures stable ordering across implementations.
- `separators=(",", ":")` removes all whitespace.
- `default=str` makes `datetime` objects serialize via `str()` rather than raising; this is identical to the Polaris rule.
- The output is bytes (UTF-8), never `str`. Implementations in other languages MUST produce identical bytes.
- ASCII output is NOT enforced. If a field contains non-ASCII characters, they pass through encoded as UTF-8 escapes in the JSON. Implementers MUST use `ensure_ascii` correctly: Python's `json.dumps` defaults to `ensure_ascii=True`, which is what Cathedral expects. TypeScript implementations MUST manually escape non-ASCII to match.
- Keys listed in `EXCLUDED` are dropped from the payload before serialization. This lets a single dict carry both the signed payload and the signature field without circular self-reference at signing time.

### 7.2 BLAKE3 hashing

All content hashes in this spec are BLAKE3, encoded as **lowercase hexadecimal, 64 characters**. The hash inputs are:

| Hash | Input bytes |
|------|-------------|
| `bundle_hash` | The raw bytes of the uploaded `.zip` file BEFORE encryption. |
| `output_hash` | `canonical_json(output_card_dict)` per 7.1. |
| `task_hash` | `canonical_json(task_spec_dict)` per 7.1. |

Implementations MUST agree on which bytes go in. The Polaris side computes `output_hash` from the card dict it persists to its run record; the Cathedral side recomputes from the bytes it receives. If there is any divergence (Pydantic round-trip differences, datetime serialization quirks, field ordering), the hashes will not match and the attestation will fail to verify. The contract tests in CONTRACTS.md §8 pin reference vectors that both sides must reproduce.

### 7.3 Timestamps

ISO-8601 UTC, millisecond precision, mandatory `Z` suffix. Format: `YYYY-MM-DDTHH:MM:SS.sssZ`. Examples: `2026-05-10T18:00:00.000Z`, `2026-05-10T18:00:00.123Z`. Microsecond precision is rejected. No timezone offset other than `Z` is allowed. The publisher uses `datetime.now(UTC)` truncated to milliseconds; miners and attesters MUST produce values in this exact format.

### 7.4 Field encoding

- ss58 hotkeys: ASCII, 47 to 48 chars, no leading or trailing whitespace.
- Base64: standard (RFC 4648), with padding. URL-safe base64 is NOT accepted.
- Hex: lowercase, no `0x` prefix, no separators.

---

## 8. Submission endpoint behavior

### 8.1 Endpoint

`POST /v1/agents/submit` (CONTRACTS.md §2.1) accepts the existing multipart fields plus a new optional `attestation` field.

### 8.2 Validation order

The publisher MUST perform validation in this exact order. The order matters because earlier checks gate access to expensive operations (signature verification, Hippius storage):

1. Form shape: required fields present, `bundle` is a `.zip`.
2. Bundle size cap (≤ 10 MiB; → 413).
3. Bundle structure validation (`validate_hermes_bundle`; → 422 on malformed).
4. `bundle_hash = blake3(raw_bytes)`.
5. `card_definitions[card_id]` lookup; must be `active` (→ 404).
6. Clock-skew window on client-supplied `submitted_at` (→ 400 if > 5 min).
7. Hotkey signature verification (`X-Cathedral-Signature`; → 401 on failure).
8. **Attestation handling** (NEW, this spec):
   - If `attestation` form field is absent or empty: `attestation_tier = 'B'`; skip to step 9.
   - Else: parse attestation, dispatch on `version`, run tier-specific verification (Section 4 or 5). On failure: respond with HTTP 422 + the specific error code listed below. The bundle is NOT stored. The miner can retry with a corrected attestation or omit it to land on the discovery surface.
9. Similarity check (CONTRACTS.md §7.1).
10. Bundle encryption + Hippius upload.
11. INSERT into `agent_submissions` with the resolved `attestation_tier`.
12. Respond 202 with `{id, bundle_hash, status, attestation_tier, submitted_at}`.

### 8.3 Error codes

| HTTP | `detail` machine code | Meaning |
|------|----------------------|---------|
| 400 | `clock_skew` | Client `submitted_at` outside ±5 min window. |
| 401 | `invalid_hotkey_signature` | The `X-Cathedral-Signature` does not verify. |
| 404 | `card_not_found` / `card_not_active` | Card unknown or retired. |
| 413 | `bundle_too_large` | Bundle > 10 MiB. |
| 422 | `attestation_shape` | Attestation JSON malformed or required keys missing. |
| 422 | `attestation_unsupported_version` | `version` not in `{polaris-v1, nitro-v1, tdx-v1, sevsnp-v1}`. |
| 422 | `attestation_unsupported_platform` | TEE platform not enabled at this publisher. |
| 422 | `attestation_unknown_signer` | Polaris key not in active or historical set. |
| 422 | `attestation_signature_invalid` | Signature does not verify against the trust root. |
| 422 | `attestation_binding_mismatch` | Server-computed envelope field does not match attestation envelope. Body carries the differing field name. |
| 422 | `attestation_marketplace_mismatch` | Tier A: Polaris marketplace record does not corroborate the claim. |
| 422 | `attestation_unknown_runtime` | Measurement does not match any entry in the approved runtime registry. |
| 422 | `attestation_deprecated_runtime` | Measurement matches a runtime past its deprecation date. |
| 422 | `attestation_removed_runtime` | Measurement matches a removed runtime. |
| 422 | `attestation_stale` | `timestamp` > 48 hours older than server clock. |
| 422 | `attestation_future` | `timestamp` > 5 minutes in the future. |
| 422 | `attestation_nonce_invalid` | Tier B+: nonce unknown, already used, or expired. |
| 422 | `attestation_unknown_task` | Tier A: Polaris `task_id` does not resolve to a Cathedral `card_id`. |
| 409 | `duplicate_submission` / `exact_bundle_duplicate` | Already-submitted bundle (CONTRACTS.md L7). |
| 503 | `bundle_storage_unavailable` | Hippius unreachable. |

A submission can never be rejected for "lacking an attestation." An empty attestation field always succeeds at Tier B. The 422 attestation errors only fire when an attestation is *attempted* and *fails*. The miner's choice is binary: attempt verified entry (and accept the risk of 422), or skip the attestation and land on discovery.

### 8.4 Nonce endpoint

`GET /v1/attestation/nonce?hotkey=<ss58>` issues a single-use nonce for use in a TEE attestation `user_data` or `report_data` field. Response:

```json
{
  "nonce": "<32-byte lowercase hex>",
  "issued_at": "2026-05-10T18:00:00.000Z",
  "expires_at": "2026-05-10T19:00:00.000Z"
}
```

The nonce is generated from `secrets.token_bytes(32)` and persisted in `attestation_nonces (hotkey, nonce_hex, issued_at, expires_at, consumed_at)`. The publisher consumes the nonce on successful Tier B+ verification by setting `consumed_at`. Re-use of a consumed nonce → 422 `attestation_nonce_invalid`. Nonces older than 1 hour are rejected at consumption time.

This endpoint is unauthenticated but rate-limited per IP (60/hour) and per hotkey (10/hour). Tier A submissions do NOT use this endpoint; Polaris attestations carry their own freshness signal via `payload.completed_at` plus the Polaris marketplace cross-reference.

---

## 9. Threat model

Each tier protects against a different set of attacks. The validator MUST treat the three tiers as carrying different residual risks even though they receive the same `verified=true` flag in the database. Forwarding "the leaderboard is verified" as a single unqualified claim is a category error.

### 9.1 Tier A: Polaris-hosted

**Trust assumption:** Polaris's operational integrity, including their key management, their attestation service correctness, and the integrity of their internal marketplace record.

**Protected against:**

- Miner forging an output card the bundle didn't actually produce. The output_hash is part of the Polaris signature; Polaris signs over what its own run actually emitted.
- Miner attributing another miner's bundle to their hotkey. The marketplace cross-reference (4.3 step 5) binds the submission_id to the worker_hotkey.
- Replay of an older Polaris attestation against a current submission. The bundle_hash + task_hash binding means the attestation cannot apply to a different bundle or a different task.

**NOT protected against:**

- Compromise of Polaris's signing key. If the `POLARIS_CATHEDRAL_SIGNING_KEY` is leaked, an attacker can mint arbitrary Tier A attestations until the key is rotated and the old key is removed from the historical list. Mitigation: short rotation cadence (90 days) and the historical-list bound (5 keys).
- Collusion between a miner and a Polaris operator with key access. There is no in-band signal Cathedral can use to detect this. Mitigation: this is a known property of any single-trust-root system; Tier B+ exists partly to give miners and downstream consumers a non-Polaris path.
- Polaris running a different bundle than the one the miner uploaded. The Polaris attestation signs the output_hash AND the task_hash but does NOT directly sign the bundle_hash in v1's polaris-v1 format (it signs `submission_id` which references the bundle internally on Polaris's side). This is a known v1 gap; the marketplace cross-reference partially closes it by tying submission_id to worker_hotkey, but a fully byte-bound bundle_hash would require an extension to polaris-v1. Tracked for v1.1.

**Failure mode summary:** Polaris compromise or collusion. Acceptable for v1 because Polaris is Cathedral's biz partner and the failure is at least detectable (key rotation, cross-reference checks).

### 9.2 Tier B+: Self-TEE

**Trust assumption:** The hardware vendor's certificate chain (AWS / Intel / AMD) is valid and the vendor is not maliciously issuing certificates to attackers; the TEE primitive's user-data binding works as specified; the approved runtime registry correctly enumerates valid Hermes builds.

**Protected against:**

- Miner running a modified Hermes that emits forged outputs. The measurement check rejects any runtime not in the registry.
- Miner running Hermes on un-attested hardware. The vendor signature chain is required.
- Replay of an old attestation. The nonce check requires the report to bind a nonce that Cathedral issued recently.
- Forged outputs from a miner who controls their hardware but not the TEE. The user-data binding means the attestation only validates for the exact envelope (bundle_hash, output_hash, task_hash) that the running enclave computed.

**NOT protected against:**

- TEE side-channel attacks. Spectre-family, Foreshadow, Plundervolt, ZombieLoad and successors are out of scope for what the attestation can prove. A sophisticated attacker who can leak secrets from a measured enclave still cannot directly forge an attestation, but they can leak the encryption key Cathedral uses internally for bundle storage if they happen to have access to that. Mitigation: keep bundle encryption out of the TEE; the TEE only attests to runtime measurement and output integrity.
- Vendor revocation. If AMD revokes a VCEK or AWS revokes a Nitro certificate, attestations signed under that cert become invalid. v1 does not poll the vendor revocation lists. Mitigation: operationally check vendor advisories quarterly; reject attestations whose certs are listed in known-revoked sets at config-load time.
- Nation-state-level attacks. Vendor key compromise at the manufacturer level, supply-chain attacks against the silicon, etc. These are out of scope for a regulatory intelligence subnet's threat budget.
- A miner who buys a TEE machine, runs an approved Hermes build, and feeds it deliberately misleading source material. The attestation will validate. The card will rank by Cathedral's scoring rules, which catch low-quality sources via the source-quality dimension (SCORING.md). The attestation does not claim "the content is correct"; only "this approved runtime produced this output for this task."

**Failure mode summary:** Vendor cert chain compromise, side-channel attacks, or a sophisticated attacker with hardware-level access. Acceptable for v1 because these are public-knowledge TEE limits and the residual risk is shared with every cloud platform.

### 9.3 Tier B: Unverified

**Trust assumption:** None. The submission carries the miner's hotkey signature over the bundle and the card, which proves the miner intends to attach their hotkey to the artifact, but does NOT prove the bundle produced the card. The miner may have hand-crafted the card. They may have run an unmodified Hermes on their laptop with no attestation. They may have generated the card with a different agent entirely and uploaded a placeholder bundle.

**Protected against:**

- Outright impersonation of another miner. The hotkey signature is required.
- Replay of someone else's bundle as your own. The submission UNIQUE constraint and similarity check (CONTRACTS.md §7.1) catch this.

**NOT protected against:**

- Anything else. There is no provenance claim.

**Failure mode summary:** No trust assumption is made; the discovery surface treats Tier B as research material. Buyers of Tier B cards know they are buying unverified work.

### 9.4 Cross-tier interactions

The leaderboard MUST display the attestation_tier alongside the rank. A Tier A entry and a Tier B+ entry occupying ranks 1 and 2 carry different residual risks, and downstream consumers (regulatory teams, journalists, researchers) need that information. The same is true for the first-mover anchor (CONTRACTS.md §7.2): a Tier A first mover is a different signal than a Tier B+ first mover. Cathedral's UI MUST surface this; the API MUST include `attestation_tier` in every leaderboard and submission response.

---

## 10. Verification reference implementation

The pseudocode below is illustrative, not normative. The normative behavior is the prose above. Where pseudocode and prose conflict, the prose wins.

### 10.1 Common envelope validation

```python
def validate_envelope(env: dict, *, server_bundle_hash: str,
                      server_output_hash: str, server_task_hash: str,
                      server_hotkey: str) -> None:
    REQUIRED = {"attestation_version", "bundle_hash", "card_id",
                "miner_hotkey", "output_hash", "task_hash", "timestamp"}
    missing = REQUIRED - env.keys()
    if missing:
        raise AttestationError("attestation_shape", f"missing: {missing}")
    if env["bundle_hash"] != server_bundle_hash:
        raise AttestationError("attestation_binding_mismatch", "bundle_hash")
    if env["output_hash"] != server_output_hash:
        raise AttestationError("attestation_binding_mismatch", "output_hash")
    if env["task_hash"] != server_task_hash:
        raise AttestationError("attestation_binding_mismatch", "task_hash")
    if env["miner_hotkey"] != server_hotkey:
        raise AttestationError("attestation_binding_mismatch", "miner_hotkey")
    _check_freshness(env["timestamp"])
```

### 10.2 Tier A (Polaris)

```python
def verify_polaris_v1(attestation: dict, envelope: dict) -> None:
    # Shape
    if attestation.get("version") != "polaris-v1":
        raise AttestationError("attestation_unsupported_version")
    payload = attestation["payload"]
    sig_b64 = attestation["signature"]
    pubkey_hex = attestation["public_key"]

    # Pinning
    active = bytes.fromhex(os.environ["POLARIS_ATTESTATION_PUBLIC_KEY"])
    historical = [bytes.fromhex(k) for k in
                  os.environ.get("POLARIS_ATTESTATION_PUBLIC_KEYS_HISTORICAL", "").split(",") if k]
    supplied = bytes.fromhex(pubkey_hex)
    if supplied not in {active, *historical}:
        raise AttestationError("attestation_unknown_signer")

    # Signature
    pk = Ed25519PublicKey.from_public_bytes(supplied)
    blob = canonical_json(payload)
    try:
        pk.verify(base64.b64decode(sig_b64), blob)
    except InvalidSignature:
        raise AttestationError("attestation_signature_invalid")

    # Envelope mapping → common envelope
    common = {
        "attestation_version": "polaris-v1",
        "bundle_hash": envelope["bundle_hash"],     # server-trusted
        "card_id": envelope["card_id"],
        "miner_hotkey": envelope["miner_hotkey"],
        "output_hash": payload["output_hash"],
        "task_hash": payload["task_hash"],
        "timestamp": payload["completed_at"],
    }
    validate_envelope(common, ...)

    # Polaris marketplace cross-reference
    record = polaris_client.get_submission(payload["submission_id"])
    if record["worker_hotkey"] != envelope["miner_hotkey"]:
        raise AttestationError("attestation_marketplace_mismatch")
    if record["deployment_id"] != payload["deployment_id"]:
        raise AttestationError("attestation_marketplace_mismatch")

    # Task-id → card-id check
    if polaris_task_to_card(payload["task_id"]) != envelope["card_id"]:
        raise AttestationError("attestation_unknown_task")
```

### 10.3 Tier B+ (Nitro example)

```python
def verify_nitro_v1(attestation: dict, envelope: dict) -> None:
    if attestation.get("version") != "nitro-v1":
        raise AttestationError("attestation_unsupported_version")

    doc_bytes = base64.b64decode(attestation["attestation_doc_b64"])
    cose = parse_cose_sign1(doc_bytes)

    # 1. Chain to AWS Nitro root
    chain = cose.protected_headers["x5chain"]
    verify_cert_chain(chain, root_pem=os.environ["AWS_NITRO_ROOT_CERT_PEM"])

    # 2. COSE signature
    leaf_cert = chain[0]
    if not cose.verify(leaf_cert.public_key()):
        raise AttestationError("attestation_signature_invalid")

    # 3. PCR0 match against approved runtime registry
    pcr0 = cose.payload["pcrs"][0].hex()
    if not approved_runtime_for(pcr0_hex=pcr0):
        raise AttestationError("attestation_unknown_runtime")

    # 4. user_data == blake3(canonical_json(envelope))
    expected_userdata = blake3(canonical_json(envelope)).digest()
    if cose.payload["user_data"] != expected_userdata:
        raise AttestationError("attestation_binding_mismatch", "user_data")

    # 5. Freshness
    issued = parse_iso(cose.payload["timestamp"])
    skew = abs((datetime.now(UTC) - issued).total_seconds())
    if skew > 48 * 3600:
        raise AttestationError("attestation_stale")
    if (issued - datetime.now(UTC)).total_seconds() > 300:
        raise AttestationError("attestation_future")

    # 6. Nonce
    nonce_hex = cose.payload["nonce"].hex()
    consume_nonce_or_reject(envelope["miner_hotkey"], nonce_hex)

    validate_envelope({**envelope, "attestation_version": "nitro-v1"}, ...)
```

### 10.4 Pseudocode for TDX and SEV-SNP

Follow the same skeleton as 10.3, substituting:

- TDX: parse the binary quote; chain to the Intel PCS root; verify the PCK→ECDSA-attestation-key chain; match `mr_td` against the runtime registry; check `report_data[0:32] == blake3(canonical_json(envelope)).digest()`; nonce in `report_data[32:64]`.
- SEV-SNP: parse the 1184-byte report; chain to ARK/ASK; verify with VCEK; match `measurement` against the runtime registry; check `report_data[0:32]` and `report_data[32:64]` for envelope hash and nonce.

The vendor SDK provides the parsing primitives in all three cases. Cathedral's verification code is a thin shim that wires the vendor SDK into the common envelope check.

### 10.5 Example: Tier A polaris-v1 payload (illustrative)

The bytes that get signed for a representative Polaris attestation:

```
{"completed_at":"2026-05-10T18:00:00.123Z","deployment_id":"dep_01H7XJ2K8N3Y0M6S9P4QV","output_hash":"3f0a2b4c8e1d6f5a9c3b2e1d4f7a8c9b6e5d4c3b2a1f0e9d8c7b6a5f4e3d2c1b","submission_id":"sub_01JABCDXYZQRSTUVWXYZ","task_hash":"a9b8c7d6e5f4030201f0e9d8c7b6a5f4e3d2c1b0a9b8c7d6e5f4030201f0e9d8","task_id":"task_eu-ai-act_2026w19"}
```

(Single line, sorted keys, no whitespace, UTF-8.) The base64 Ed25519 signature over those bytes goes in the `signature` field of the outer attestation envelope. Both Polaris and Cathedral MUST produce identical bytes when they canonicalize the payload, or verification fails.

### 10.6 Example: Common envelope (Tier B+ Nitro)

```
{"attestation_version":"nitro-v1","bundle_hash":"7c8d9e0f1a2b3c4d5e6f70819203a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5","card_id":"eu-ai-act","miner_hotkey":"5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY","output_hash":"3f0a2b4c8e1d6f5a9c3b2e1d4f7a8c9b6e5d4c3b2a1f0e9d8c7b6a5f4e3d2c1b","task_hash":"a9b8c7d6e5f4030201f0e9d8c7b6a5f4e3d2c1b0a9b8c7d6e5f4030201f0e9d8","timestamp":"2026-05-10T18:00:00.123Z"}
```

`blake3(<above bytes>)` is what the enclave binds into the Nitro `user_data` field.

---

## 11. Migration / v2 outlook

### 11.1 zkML / SNARK attestation

A future Tier B++ would accept zero-knowledge proofs of inference, allowing a miner to prove a card was produced by an approved model on inputs whose hash matches the task spec, without revealing the input or the model weights to Cathedral. Current state of the art (Halo2, EZKL, etc.) is roughly 1000 to 10000 times slower than native inference for models in the Hermes size class and would impose a card-production budget that doesn't match Cathedral's epoch cadence. Tracked for v2.

The envelope and verification flow generalize: a `zkml-v1` attestation would carry a SNARK proof and a verifying key, the verification step would call the SNARK verifier instead of an Ed25519 / COSE signature check, and the runtime registry would carry circuit hashes instead of (or in addition to) image measurements.

### 11.2 Cross-tier challenges

Today, the leaderboard is a single ordering. A v2 governance feature would allow holders of a verified submission (or external auditors with standing) to formally challenge a rank by producing evidence that the attestation, while cryptographically valid, was not behaviorally honest. Example: a Polaris-attested run on a forked Hermes that was later removed from the registry, or a TEE attestation from a known-revoked VCEK.

The structure would be:

- A challenge is posted on-chain (Bittensor extrinsic) referencing the submission id.
- A challenge window opens (default 7 days); the challenged miner can respond.
- A multi-sig governance group (the validator collective, multi-sig'd) resolves the challenge.
- A resolved-as-fraud submission is removed from the leaderboard and emissions for its epoch are clawed back via the next weight set (best-effort; on-chain emissions already paid out cannot be recalled).

This is governance work, not protocol work. The cryptographic attestation already provides what it can; the rest is human + economic adjudication. Deferred.

### 11.3 Polaris polaris-v2 (bundle-hash binding)

As noted in Section 9.1, the v1 `polaris-v1` format does not sign the `bundle_hash` directly. A v1.1 release of the Polaris attestation contract should add `bundle_hash` to `payload`, giving the same byte-bound guarantee Tier B+ already has. The migration path is: Polaris adds the field with a `polaris-v2` version tag; Cathedral begins accepting `polaris-v2` in addition to `polaris-v1`; after a deprecation window, `polaris-v1` is removed.

### 11.4 Approved runtime registry on-chain

When the Hermes release cadence stabilizes (estimated v2 timeframe, late 2026), the registry should move from a git JSON file to a multi-sig-controlled chain extrinsic on the Cathedral subnet. The migration mostly affects governance and auditability; the verification code path stays the same with a different loader.

### 11.5 Vendor revocation polling

v2 should poll AWS Nitro, Intel PCS, and AMD KDS revocation endpoints on a heartbeat, cache the revoked-cert serials, and reject attestations whose cert chain intersects the revoked set. v1 handles this operationally (operator updates the env-var pinned roots on advisory).

---

End of v1 attestation contract. Implementation work is tracked under cathedral issue series #attestation-* (to be opened as PRs against this spec land).
