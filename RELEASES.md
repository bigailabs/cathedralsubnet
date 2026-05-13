# Releases

Canonical release notes for the Cathedral subnet. Mirrored to GitHub at
[github.com/cathedralai/cathedral/releases](https://github.com/cathedralai/cathedral/releases).

Versioning follows the runtime image: `v<major>.<minor>.<patch>` tracks
`docker/cathedral-runtime/CATHEDRAL_RUNTIME_VERSION`. A new tag is cut
whenever the producer-side surface changes in a way miners or validators
need to know about.

---

## v1.1.7 — Prober: hermes chat -q (full agentic loop)

**Date:** 2026-05-13

**Headline:** The SSH prober now invokes `hermes chat -q` (full agentic
loop with tool calls + skill execution + multiple model turns + memory
reads) instead of `hermes -z` (one-shot `model.generate(prompt)` with
no agent loop). Cathedral's value prop is verifying the agent actually
did work — fetching source URLs, calling tools, reasoning across
turns. The `-z` invocation stripped exactly that out.

### Fixed

- **Prober no longer bypasses the Hermes agent loop.** `hermes -z` was
  a one-shot model call: no tool calls, no skill execution, no URL
  fetching, no memory reads. Iota1's test tonight (submission
  `13bb4a12`) confirmed the failure mode: Hermes returned "I apologize
  for the difficulties in accessing real-time information about
  Article 5 of the AI Act..." because `-z` doesn't run the agent that
  would have fetched the source. v1.1.7 switches to `hermes chat -q`,
  which runs the full Hermes agent loop end-to-end.
- **Trace bundles now capture the full tool-call history.** Request
  dumps, session messages, and the SQLite slice now reflect a real
  agentic run rather than a single API call. Proof-of-loop counts
  (`tool_call_count`, `api_call_count`, `tool_calls_observed`) become
  the real verifiable-work signal.

### Changed

- **`SshHermesRunnerConfig.eval_timeout_secs` default raised from 120s
  to 300s.** The agentic loop has to think, call tools, wait for tool
  results, and think again, so the one-shot 120s budget is too tight.
  The orchestrator's `CATHEDRAL_SSH_EVAL_TIMEOUT` env default also
  moves from 600s → 300s for consistency (was already 600s in the
  env-driven path).
- **Dropped `SshHermesRunnerConfig.eval_max_turns` and the
  `CATHEDRAL_HERMES_MAX_TURNS` env wrapper.** `hermes chat -q` has no
  equivalent CLI flag, and we want the full agentic loop now rather
  than a single-turn cap. The old field was already inert on Hermes
  0.13.0 (the `--max-turns` flag does not exist in 0.13.0; v1.1.5
  stopped passing it).

### For miners

No miner-side change required. The same `~/.hermes/` skills you've
already shipped now get exercised by the agent during the eval round.
If your `soul.md` instructs the agent to cite sources, the agent will
now actually fetch those sources rather than hallucinating around the
gap. Trace bundles get richer; scoring weights are unchanged.

---

## v1.1.4 — Miner-onboard UX: resubmits + public failed-evals

**Date:** 2026-05-12

**Headline:** Two production UX fixes from tonight's first-miner
onboarding pass. Miners can resubmit under their own hotkey after a
failed eval without inventing new display_names, and the public API
now exposes failed eval attempts so the leaderboard's empty-state can
surface real network activity even before any agent scores above zero.

### Fixed

- **Same-hotkey resubmits are no longer blocked by the 7-day fuzzy
  display_name dedupe.** The check was intended to stop OTHER miners
  from squatting an existing display_name; a miner resubmitting under
  their own hotkey (e.g. after a failed eval) should not have to mint
  `McDEE-v2` / `McDEE-v3` / etc to bypass their own prior rows. The
  fuzzy candidate set now excludes the submitter's own hotkey. The
  cross-hotkey squat path is unchanged: a different miner submitting
  the same or fuzzy-close display_name within 7 days is still
  rejected (`status=rejected` with `rejection_reason` set).

### New

- **`GET /api/cathedral/v1/cards/{card_id}/attempts?limit=20`** — public
  endpoint returning recent `eval_runs` for a card INCLUDING failed
  ones (`_ssh_hermes_failed=true` and/or `weighted_score=0`). Same
  per-row shape as `/feed` plus a `miner_hotkey` field for
  attribution. Ordered `ran_at DESC`, default 20 rows, max 200. Lets
  the public leaderboard surface real network activity even before
  any submission scores above zero (cathedralai/cathedral PR #119
  empty-state design).

### For miners

If your eval failed and you want to resubmit under the same display_name,
just do it — same hotkey, same name, different bundle is now the normal
recovery path. No need to suffix v2 / v3 / etc.

---

## v1.0.7 — Polaris-native v2 runtime, two-tier mining

**Date:** 2026-05-12

**Headline:** Cathedral now runs every miner's agent inside a real Hermes
container on Polaris (paid, attested, 1.10x multiplier) or queries the
miner's own Hermes via SSH (free, observed). Both flow through the same
six-dimension scoring pipeline and the same Cathedral signing chain.

### New

- **Tier A — `polaris-deploy`**: publisher asks Polaris to deploy the
  miner's bundle as a real Hermes agent on a Verda VM. Polaris signs a
  runtime attestation; Cathedral re-derives every hash and verifies before
  scoring. Earns the **1.10x verified-runtime multiplier**.
- **Tier B — `ssh-probe`**: miner runs Hermes themselves on any host with
  SSH access. They authorize Cathedral by adding the platform SSH key
  (`/.well-known/cathedral-ssh-key.pub`) to `authorized_keys`. Cathedral
  SSHs in, queries the local `/chat`, leaves. No 1.10x multiplier; the
  runtime is observed, not attested.
- **Submit endpoint** accepts `attestation_mode` (`polaris-deploy` /
  `ssh-probe` / `tee` / `unverified` / `bundle`) plus optional
  `ssh_host` / `ssh_port` / `ssh_user` / `hermes_port` for free-tier
  registration.
- **`PolarisDeployRunner`** and **`SshProbeRunner`** added under
  `cathedral.eval.*`. Both produce the canonical `PolarisRunResult` shape;
  scoring + signing path is unchanged.
- **Hermes bundle ingest**: the Polaris-deployed Hermes container fetches
  the encrypted bundle from R2 at startup, decrypts with the env-injected
  KEK + `encryption_key_id`, and extracts into its profile dir. Skills /
  AGENTS.md / soul.md become the running agent's identity.
- **Structured execution trace** in the `/chat` response: tool calls
  (redacted args schema), model calls (model + tokens + latency), agentic
  loop depth, start/end timestamps. Persisted to `eval_runs.trace_json` as
  an unsigned sidecar so old validators continue to verify signatures
  unchanged.
- **CHECK constraint widening** for `agent_submissions.attestation_mode`
  via a 12-step SQLite ALTER procedure (the previous constraint rejected
  `polaris-deploy` and `ssh-probe` on legacy volumes; SQLite cannot ALTER
  a CHECK in place).
- **Site additions**: `/verification` page enumerates exactly what each
  signature attests + what v1 does NOT yet verify; submit form on
  `/jobs/<id>/submit` now has a tier picker that conditionally reveals
  SSH coordinates for Tier B.
- **`docs/VALIDATOR.md`** + **`docs/validator/RUNBOOK.md`** updated for
  the v2 dispatch flow.
- **Universal Cathedral SSH key** published at
  `https://api.cathedral.computer/.well-known/cathedral-ssh-key.pub` for
  free-tier miner installation.

### Changed

- Default `attestation_mode` is now `bundle` (BYO-compute) instead of
  legacy `polaris`. The legacy `polaris` runtime-evaluate shim path is
  retained as a backup but no longer the default — production load goes
  through `polaris-deploy` (the new path) for new submissions.
- Publisher dispatcher (`_runner_for`) now routes all five attestation
  modes correctly; previously only `polaris` and `tee` were wired.
- Polaris's `TemplateDeploymentRequest.validate_parameters` widened to
  admit base64 (`+ /`) and URL query (`? & % # ~`) characters in
  `env_vars` values so presigned URLs and wrapped keys can pass through
  to the spawned Hermes container.

### Operational

- `ghcr.io/cathedralai/cathedral-runtime:v1.0.7` published + public.
- Railway env on `cathedral-publisher`:
  - `CATHEDRAL_PIN_CHUTES_KEY=true` (Cathedral forwards its Chutes key
    to spawned Hermes containers for Tier A miners).
  - `CATHEDRAL_PROBE_SSH_PUBLIC_KEY` set to the platform-wide pubkey.

### Known limitations

- Live verification chain (`set_weights` on SN292 testnet, weekly Merkle
  anchor on chain) wired in code but not yet running against the live
  publisher signature stream.
- v1 runtime treats `soul.md` as the LLM system prompt rather than
  spinning up the full Hermes agentic loop with tool routing. v2.1 closes
  that gap (tracked in `cathedral-redesign/OBSERVABILITY_V2.md`).
- Tier B SSH-probe live miner pipeline is in code + tested, awaiting the
  first end-to-end Tier B card.

### For miners

Start here: `curl https://api.cathedral.computer/skill.md`.

Tier A (paid, recommended): no extra setup; just `POST /v1/agents/submit`
with `attestation_mode=polaris-deploy`.

Tier B (free, BYO infrastructure): rent or own a box with Hermes running,
register SSH coordinates with your submission, install the platform SSH
key in `authorized_keys`.

### For validators

`docs/validator/RUNBOOK.md` is the canonical setup guide.

```bash
git clone https://github.com/cathedralai/cathedral
cd cathedral
bash scripts/provision_validator.sh
```

Validators verify the Cathedral signature on every `EvalRun` projection
locally against a pinned pubkey. Polaris attestations and miner-hotkey
signatures are verified upstream by the publisher.

### Signed commits

Every commit landing on `main` from 2026-05-12 onward is SSH-signed and
shows as "Verified" on GitHub, so validators auditing the source tree can
trust each revision back to a known maintainer key.
