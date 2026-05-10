# Validator Handoff

A clean handoff is a property of the system, not a one-time event. If you cannot hand the validator to another operator with this document plus credentials, the handoff itself is broken — file an issue.

## What the incoming operator gets

1. This document
2. `docs/validator/RUNBOOK.md`
3. `config/<network>.toml` (with `validator_hotkey` filled in)
4. Hotkey + coldkey files (encrypted at rest)
5. A new bearer token (rotate on every handoff)
6. The Polaris public key hex (same across operators)
7. Read access to the chain, Polaris API, and any monitoring dashboards

## What does not get handed over

- Tribal knowledge. If something matters, it goes in the runbook.
- The previous operator's bearer token (rotated).
- Personal credentials of the previous operator.

## Cutover steps

1. Incoming operator stands up the validator on their host using this repo + `cargo build --release`
2. Validator runs in dry mode for at least one weight-set interval (`weights.disabled = true`)
3. `cathedral health` reports `registered: true`, `weight_status: disabled`, no `stalled`
4. Outgoing operator stops their validator
5. Incoming operator flips `weights.disabled = false`, restarts
6. Watch one full weight-set cycle; confirm `weight_status: healthy`

## When something goes wrong

- Roll back: outgoing operator restarts their validator with the old bearer
- File an issue with the runbook gap
- Update `RUNBOOK.md` before retrying
