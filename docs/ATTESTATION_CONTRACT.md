# Cathedral v1 Attestation Contract

Status: v1 draft, 2026-05-10.
Scope: defines how miners prove that the agent named in a Cathedral submission produced the output card the submission carries.
Audience: Cathedral validator implementers, miners building self-TEE compute paths, downstream auditors.
Companion documents: `cathedral-redesign/CONTRACTS.md` (data contracts), `docs/protocol/CLAIM.md` (legacy `/v1/claim`), `docs/protocol/SCORING.md` (card scoring).

> **v1 live-path note.** The live path in v1 is `attestation_mode='ssh-probe'` (BYO Box; Cathedral SSHs into the miner-declared host and invokes `hermes chat -q "<task>"`). Future cryptographic-attestation paths are specified below for implementers, but they are not part of the live miner/operator path.

> This spec is the layer that decides which submissions earn TAO emissions and rank on the leaderboard, and which submissions live only on the discovery surface. It does not define how cards are scored. It does not define on-chain weight setting. It defines the *trust statement* that gates entry to those flows.

---

## 1. Purpose & non-goals

### 1.1 Purpose

Cathedral is a regulatory-intelligence subnet. Miners submit Hermes-compatible bundles that consume a card task, produce a card JSON, and ship the resulting artifact to the Cathedral publisher. The publisher must answer one question before that submission can earn emissions or rank on the leaderboard: *did the bundle named in this submission actually produce this output?*

There are operationally distinct ways to answer that question, each with a different trust root, and this document specifies the wire format, verification procedure, and threat model for the paths that are documented for v1. The paths are not interchangeable. A submission verified behaviorally by ssh-probe is not the same as a submission verified by AWS Nitro; they protect against different failure modes and ride different revocation calendars. The validator must track which path each submission used and which root of trust applied.

The document is written as an RFC. It is normative for any validator that emits Cathedral weights and for any miner that wants their submission to count for emissions. The HTTP endpoint behavior in Section 8 is enforced by the publisher; everything else is enforced by the validator's worker loop and the leaderboard publisher.

### 1.2 Non-goals

This document does NOT cover:

- Card scoring. See `docs/protocol/SCORING.md`.
- Bundle format, Hermes runtime, or eval orchestration. See `cathedral-redesign/CONTRACTS.md` §6.
- On-chain weight setting or Merkle anchoring. See `CONTRACTS.md` §4.5 and §4.6.
- zkML or SNARK-based proof of inference. Noted in Section 11 as v2 future work.
- Cross-tier challenge governance (one miner disputing another's verified tier). Noted in Section 11.

### 1.3 What "attestation" means here

In this spec, an **attestation** is a signed structured statement that binds the following four facts together as a single cryptographic claim:

1. The bundle the miner submitted (`bundle_hash`).
2. The task Cathedral specified (`task_hash`).
3. The output card the bundle produced (`output_hash`).
4. The identity of the miner (`miner_hotkey`).

A signature alone over the output is not an attestation. A bundle hash alone is not an attestation. The combined binding is what lets the validator say "this hotkey caused this bundle to produce this output for this task." Each tier produces this binding from a different trust root.

---

## 2. Verification overview

> **Terminology note for v1.** This contract uses the word "attestation" narrowly, for cryptographic attestation documents (AWS Nitro / Intel TDX / AMD SEV-SNP). The v1 live path, `ssh-probe`, is a separate mechanism: behavioral verification by Cathedral SSHing into the miner-declared host and invoking Hermes itself during the eval window. Wherever this document says "attestation," it means the cryptographic kind. Wherever it says "ssh-probe" or "behavioral verification," it means the live v1 path. Both produce leaderboard-eligible, emissions-earning submissions; only the verification mechanism differs.

| Path | How verified | Earns TAO emissions? | Leaderboard rank? | Discovery surface? | Live in v1? |
|------|--------------|----------------------|-------------------|--------------------|-------------|
| **BYO Box (ssh-probe)** | Behavioral verification (Cathedral SSHs in and invokes Hermes during the eval window; trace bundle captured) | Yes | Yes | Yes | Yes; this is the live v1 path |
| **self-TEE** | Cryptographic attestation (hardware: AWS Nitro / Intel TDX / AMD SEV-SNP) | Yes | Yes | Yes | No; Nitro verifier wired, no live TEE miners |
| **Unverified (discovery)** | Nothing | NO | NO | Yes (browsable + purchasable, never ranked) | Yes |

ssh-probe submissions do not produce a cryptographic attestation, and they are not required to. Cathedral verifies them behaviorally by invoking Hermes itself during the eval window.

### 2.1 Verified vs discovery: the surface split

The leaderboard is verified-only, but "verified" in v1 covers two distinct mechanisms:

- **Behavioral verification** via `ssh-probe`: Cathedral SSHs into the miner-declared host and invokes Hermes itself during the eval window. The runner captures the trace bundle and treats the produced card as the agent's output. ssh-probe submissions earn TAO emissions and rank on the leaderboard. This is the live v1 path; it does not produce a cryptographic attestation (`polaris_attestation` is `None`).
- **Cryptographic attestation** via future or spec-only modes: a signed structured statement that validates under the rules below. Those paths are not the live miner/operator path in v1.

Unverified (`unverified`) submissions never enter the eval queue, never rank on the leaderboard, and never earn TAO emissions. They are persisted with `status='discovery'`, encrypted at rest like every other bundle, and surfaced on the discovery / research marketplace, which is a separate product. The discovery surface is browsable, search-indexed, and the bundles can be purchased per-card or per-bundle, but they are never ranked against verified submissions.

This is a deliberate product split, not a soft filter. The publisher persists every submission with an `attestation_mode` (`ssh-probe`, `tee`, `unverified`, plus historical modes retained in the schema) and the leaderboard / weight-loop pipeline filters out `unverified` via the standard repository filters that include `ssh-probe`. The discovery API reads the full table. The current weight loop reads from `pulled_eval_runs`, which is populated from the publisher's signed `EvalRun` projections and excludes `unverified` because those rows never produce an `EvalRun`.

### 2.2 Why keep discovery separate

The eligible Cathedral mining population is broader than live agents alone. Some useful regulatory intelligence will come from researchers and analysts who do not run agents at all; they assemble cards by hand or with home-grown tooling, and the useful product surface for them is discovery.

Collapsing discovery into "rejected" would lose this material. The split is durable. It is encoded in the database, the API, the validator weight loop, and the on-chain anchor.

### 2.3 Tier promotion and demotion

A submission's verification path is fixed at submission time. There is no in-place promotion from discovery to a verified path. To change paths, the miner re-submits the bundle through the appropriate endpoint and receives a new submission id. The original submission remains on the discovery surface unless the miner explicitly retires it.

This rule is non-negotiable. Allowing post-hoc tier promotion would require the publisher to recompute the first-mover anchor (CONTRACTS.md §7.2), the Merkle root (§4.5), and the leaderboard ranks across epochs that have already been published and anchored on-chain. The cost is not worth the convenience.

---

## 3. Common attestation envelope

Every attestation, regardless of tier, MUST carry the seven core fields below. The publisher rejects any attestation missing or malformed at the field level before the tier-specific signature verification runs. Fields are listed in their canonical JSON sort order.

| Field | Type | Description |
|-------|------|-------------|
| `attestation_version` | string | Per-attestation version tag (e.g. `nitro-v1`, `tdx-v1`, `sevsnp-v1`). |
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
                              |   nitro-v1    -> Section 4.1              |
                              |   tdx-v1      -> Section 4.2              |
                              |   sevsnp-v1   -> Section 4.3              |
                              | anything else -> 422 unsupported_version  |
                              +-------------------------+-----------------+
                                                        |
                                                        v
                              +-------------------------------------------+
                              | path-specific signature + measurement     |
                              | check; on pass -> verified self-TEE path  |
                              | set; on fail -> 422 with reason           |
                              +-------------------------------------------+
```

Note that the publisher does NOT trust the field values inside the attestation envelope for `bundle_hash`, `output_hash`, or `miner_hotkey`. It computes those server-side and requires the attestation to MATCH them. The signature verification then happens over the locked, server-trusted bytes: the attestation is only valid if the signer signed the same statement the server already knows to be true. This forecloses the entire family of attacks where a miner submits a different bundle than the one they attested to.

`task_hash` is server-derived. The publisher computes the canonical hash of the current `card_id` task spec at submission time and rejects any attestation whose `task_hash` does not match. This means an attestation generated against an outdated task spec is invalid even if it verifies cryptographically. Task specs evolve; see Section 6 on the approved runtime registry for how versioning interacts.

### 3.1 Submission carriage

Cryptographic self-TEE submissions go to `POST /v1/agents/submit` (CONTRACTS.md §2.1) and attach an additional multipart form field `attestation` containing the attestation JSON as a UTF-8 string. The form field is required only when `attestation_mode` selects a cryptographic verifier (`tee`). The v1 live `ssh-probe` mode does not attach this field; it is verified behaviorally from `ssh_host`, `ssh_port`, and `ssh_user`, enters the eval queue, and earns emissions. `attestation_mode='unverified'` is the discovery-only path and also does not attach this field. For backward compatibility, omitting `attestation_mode` defaults to `ssh-probe`, not discovery.

The existing `X-Cathedral-Signature` header continues to carry the miner's sr25519 hotkey signature over the canonical submission payload (CONTRACTS.md §4.1). For cryptographic modes, the attestation is a *separate* signed object, signed by a hardware vendor, and bound to the miner's hotkey through the `miner_hotkey` field inside the envelope. Both signatures must verify before the submission can enter that cryptographic path.

---

## 4. self-TEE attestations

self-TEE accepts hardware-vendor attestations from three platforms. The structural pattern is the same in all three cases: the vendor's attestation document contains (a) a measurement of the runtime image, (b) a vendor signature chain back to the vendor's published root certificate, and (c) a "user data" or "report data" field into which the runtime binds a hash of the workload-specific data (here: the common envelope from Section 3). Cathedral verifies the signature chain, checks the measurement against the approved runtime registry (Section 6), and confirms the user-data binding.

The three subsections below state what Cathedral validates. They do NOT restate the full attestation document format; that is documented by the vendors. Implementers MUST read the vendor docs cited in each subsection.

### 4.1 AWS Nitro Enclaves

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

On all six steps passing: persist the attestation and mark the submission as verified under the self-TEE path.

### 4.2 Intel TDX

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

On all seven steps passing: persist the attestation and mark the submission as verified under the self-TEE path.

### 4.3 AMD SEV-SNP

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

On all seven steps passing: persist the attestation and mark the submission as verified under the self-TEE path.

### 4.4 Common TEE concerns

All three subsections share three properties that Cathedral relies on:

- **User-data binding.** The TEE allows the running workload to bind arbitrary 32-or-64-byte data into the attestation document. Cathedral always uses this slot to carry the BLAKE3 of the canonical envelope JSON, plus the nonce. This is the field that turns a generic "this is a measured TDX VM" attestation into "this specific output was produced by this measured TDX VM for this specific Cathedral task."
- **Measurement match against the registry.** A signed attestation document from AWS Nitro is not sufficient on its own; it only proves an enclave ran. The PCR0 / MRTD / measurement field must match an entry in Cathedral's approved runtime image hash list (Section 6) or the attestation is rejected. This is what makes the TEE attest specifically to "running an approved Hermes runtime" and not "running anything I want."
- **Freshness via issued nonce + timestamp.** Replay protection requires both. The nonce closes replay of an older valid attestation; the timestamp closes the case where a valid nonce was issued but the report was generated outside the freshness window for some other reason (clock skew, slow CI, etc.).

The publisher MAY accept any one of the three TEE platforms; it MUST NOT accept attestations from unknown TEE platforms in v1. A `version` value outside the set `{nitro-v1, tdx-v1, sevsnp-v1}` -> 422 `attestation_unsupported_platform`.

---

## 6. Approved runtime registry

### 6.1 Format and storage

Cathedral maintains a list of approved Hermes container image hashes. Only attestations whose runtime measurement matches an entry on this list are accepted for self-TEE.

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
8. **Verification mode handling** (NEW, this spec):
   - If `attestation_mode` is absent or equals `ssh-probe`: require `ssh_host` and `ssh_user`, default `ssh_port` to 22, and continue as the live behavioral-verification path. The `attestation` form field may be absent.
   - If `attestation_mode='unverified'`: mark the submission as discovery-only; it skips eval, leaderboard, emissions, and the similarity gate below.
   - If `attestation_mode='tee'`: parse `attestation`, dispatch on `version`, run self-TEE verification (Section 4). On failure: respond with HTTP 422 + the specific error code listed below. The bundle is NOT stored. The miner can retry with a corrected attestation or explicitly choose `attestation_mode='unverified'` to land on the discovery surface.
9. Similarity check (CONTRACTS.md §7.1), skipped for `unverified`.
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
| 422 | `attestation_unsupported_version` | `version` not in `{nitro-v1, tdx-v1, sevsnp-v1}`. |
| 422 | `attestation_unsupported_platform` | TEE platform not enabled at this publisher. |
| 422 | `attestation_signature_invalid` | Signature does not verify against the trust root. |
| 422 | `attestation_binding_mismatch` | Server-computed envelope field does not match attestation envelope. Body carries the differing field name. |
| 422 | `attestation_unknown_runtime` | Measurement does not match any entry in the approved runtime registry. |
| 422 | `attestation_deprecated_runtime` | Measurement matches a runtime past its deprecation date. |
| 422 | `attestation_removed_runtime` | Measurement matches a removed runtime. |
| 422 | `attestation_stale` | `timestamp` > 48 hours older than server clock. |
| 422 | `attestation_future` | `timestamp` > 5 minutes in the future. |
| 422 | `attestation_nonce_invalid` | self-TEE nonce unknown, already used, or expired. |
| 409 | `duplicate_submission` / `exact_bundle_duplicate` | Already-submitted bundle (CONTRACTS.md L7). |
| 503 | `bundle_storage_unavailable` | Hippius unreachable. |

A submission can never be rejected merely for lacking a cryptographic attestation. Omitted `attestation` means one of two explicit modes: `ssh-probe`, the default live path that requires SSH fields and enters eval, or `unverified`, the discovery-only path. The 422 attestation errors only fire when a cryptographic attestation is attempted and fails. The miner can retry with corrected cryptographic evidence, use `ssh-probe`, or explicitly choose discovery.

### 8.4 Nonce endpoint

`GET /v1/attestation/nonce?hotkey=<ss58>` issues a single-use nonce for use in a TEE attestation `user_data` or `report_data` field. Response:

```json
{
  "nonce": "<32-byte lowercase hex>",
  "issued_at": "2026-05-10T18:00:00.000Z",
  "expires_at": "2026-05-10T19:00:00.000Z"
}
```

The nonce is generated from `secrets.token_bytes(32)` and persisted in `attestation_nonces (hotkey, nonce_hex, issued_at, expires_at, consumed_at)`. The publisher consumes the nonce on successful self-TEE verification by setting `consumed_at`. Re-use of a consumed nonce -> 422 `attestation_nonce_invalid`. Nonces older than 1 hour are rejected at consumption time.

This endpoint is unauthenticated but rate-limited per IP (60/hour) and per hotkey (10/hour).

---

## 9. Threat model

Each path protects against a different set of attacks. The validator MUST treat ssh-probe, self-TEE, and discovery as carrying different residual risks even when multiple paths are leaderboard-eligible. Forwarding "the leaderboard is verified" as a single unqualified claim is a category error.

### 9.1 self-TEE

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

### 9.2 Unverified (discovery)

This is `attestation_mode='unverified'`. It is the discovery / research-marketplace path and never earns emissions or ranks on the leaderboard. The v1 live earning path is ssh-probe (covered in §9.4 below); ssh-probe is not "unverified" and should not be conflated with this section.

**Trust assumption:** None. The submission carries the miner's hotkey signature over the bundle and the card, which proves the miner intends to attach their hotkey to the artifact, but does NOT prove the bundle produced the card. The miner may have hand-crafted the card. They may have run an unmodified Hermes on their laptop with no attestation. They may have generated the card with a different agent entirely and uploaded a placeholder bundle.

**Protected against:**

- Outright impersonation of another miner. The hotkey signature is required.
- Replay of someone else's bundle as your own. The submission UNIQUE constraint and similarity check (CONTRACTS.md §7.1) catch this.

**NOT protected against:**

- Anything else. There is no provenance claim.

**Failure mode summary:** No trust assumption is made; the discovery surface treats `unverified` as research material. Buyers of unverified cards know they are buying unverified work.

### 9.3 ssh-probe (the v1 live emissions path)

This is `attestation_mode='ssh-probe'` and is the live v1 path. It earns emissions and ranks on the leaderboard. The trust mechanism is behavioral, not cryptographic: Cathedral SSHs into the miner-declared host as the universal probe user, snapshots `~/.hermes/` into an isolated `cathedral-eval-<round>` profile, invokes `hermes chat -q "<task>"` against that profile, captures the trace bundle, and treats the produced Card JSON as the agent's output for the round. No Polaris attestation is produced (`polaris_attestation` is `None` for ssh-probe submissions).

**Trust assumption:** The miner's declared SSH endpoint is real, reachable, and runs the Hermes profile the miner advertised. Cathedral's probe key is treated as the only authorized invoker of the isolated eval profile.

**Protected against:**

- Hand-written cards posing as agent output. Cathedral itself invokes Hermes during the eval window; the card returned is the live emission of the miner's running agent, not a stored artifact pasted in after the fact.
- Output substitution between miner and validator. The trace bundle (state.db slice, sessions JSON, request dumps, skills, memories, logs) is SCP'd back as forensic evidence.

**NOT protected against:**

- A miner who runs a heavily customised Hermes build that the runtime registry would not approve for self-TEE. Behavioral verification is mechanism-agnostic; it does not measure the runtime image.
- A miner who runs an approved Hermes build but feeds it deliberately misleading source material. Source-quality scoring (SCORING.md) catches that on the content axis, not the attestation axis.
- A miner who proxies the SSH session to a different machine than they advertised. Detectable only via behavioral telemetry, not via attestation.

**Failure mode summary:** No cryptographic claim about the runtime image; the trust comes from Cathedral being the invoker. ssh-probe is sufficient for v1 because Cathedral mediates every eval directly.

### 9.4 Cross-path interactions

The leaderboard MUST display the verification mechanism alongside the rank: ssh-probe (live v1 path, behavioral) or self-TEE (hardware attestation, spec-only). Each mechanism carries different residual risks, and downstream consumers need that information. Cathedral's UI MUST surface this; the API MUST include the verification mechanism in every leaderboard and submission response.

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

### 10.2 self-TEE (Nitro example)

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

### 10.3 Pseudocode for TDX and SEV-SNP

Follow the same skeleton as 10.3, substituting:

- TDX: parse the binary quote; chain to the Intel PCS root; verify the PCK→ECDSA-attestation-key chain; match `mr_td` against the runtime registry; check `report_data[0:32] == blake3(canonical_json(envelope)).digest()`; nonce in `report_data[32:64]`.
- SEV-SNP: parse the 1184-byte report; chain to ARK/ASK; verify with VCEK; match `measurement` against the runtime registry; check `report_data[0:32]` and `report_data[32:64]` for envelope hash and nonce.

The vendor SDK provides the parsing primitives in all three cases. Cathedral's verification code is a thin shim that wires the vendor SDK into the common envelope check.

### 10.4 Example: Common envelope (self-TEE Nitro)

```
{"attestation_version":"nitro-v1","bundle_hash":"7c8d9e0f1a2b3c4d5e6f70819203a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5","card_id":"eu-ai-act","miner_hotkey":"5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY","output_hash":"3f0a2b4c8e1d6f5a9c3b2e1d4f7a8c9b6e5d4c3b2a1f0e9d8c7b6a5f4e3d2c1b","task_hash":"a9b8c7d6e5f4030201f0e9d8c7b6a5f4e3d2c1b0a9b8c7d6e5f4030201f0e9d8","timestamp":"2026-05-10T18:00:00.123Z"}
```

`blake3(<above bytes>)` is what the enclave binds into the Nitro `user_data` field.

---

## 11. Migration / v2 outlook

### 11.1 zkML / SNARK attestation

A future zero-knowledge path would accept proofs of inference, allowing a miner to prove a card was produced by an approved model on inputs whose hash matches the task spec, without revealing the input or the model weights to Cathedral. Current state of the art (Halo2, EZKL, etc.) is roughly 1000 to 10000 times slower than native inference for models in the Hermes size class and would impose a card-production budget that doesn't match Cathedral's epoch cadence. Tracked for v2.

The envelope and verification flow generalize: a `zkml-v1` attestation would carry a SNARK proof and a verifying key, the verification step would call the SNARK verifier instead of an Ed25519 / COSE signature check, and the runtime registry would carry circuit hashes instead of (or in addition to) image measurements.

### 11.2 Cross-tier challenges

Today, the leaderboard is a single ordering. A v2 governance feature would allow holders of a verified submission (or external auditors with standing) to formally challenge a rank by producing evidence that the attestation, while cryptographically valid, was not behaviorally honest. Example: a TEE attestation from a known-revoked VCEK.

The structure would be:

- A challenge is posted on-chain (Bittensor extrinsic) referencing the submission id.
- A challenge window opens (default 7 days); the challenged miner can respond.
- A multi-sig governance group (the validator collective, multi-sig'd) resolves the challenge.
- A resolved-as-fraud submission is removed from the leaderboard and emissions for its epoch are clawed back via the next weight set (best-effort; on-chain emissions already paid out cannot be recalled).

This is governance work, not protocol work. The cryptographic attestation already provides what it can; the rest is human + economic adjudication. Deferred.

### 11.3 Approved runtime registry on-chain

When the Hermes release cadence stabilizes (estimated v2 timeframe, late 2026), the registry should move from a git JSON file to a multi-sig-controlled chain extrinsic on the Cathedral subnet. The migration mostly affects governance and auditability; the verification code path stays the same with a different loader.

### 11.4 Vendor revocation polling

v2 should poll AWS Nitro, Intel PCS, and AMD KDS revocation endpoints on a heartbeat, cache the revoked-cert serials, and reject attestations whose cert chain intersects the revoked set. v1 handles this operationally (operator updates the env-var pinned roots on advisory).

---

End of v1 attestation contract. Implementation work is tracked under cathedral issue series #attestation-* (to be opened as PRs against this spec land).
