# Cathedral Subnet — Agent Notes

## Scope

This repo is the Cathedral Bittensor subnet validator + miner. It is **not** a fork of Basilica. It is a fresh implementation scoped to evidence verification.

## What Cathedral verifies

Miners submit Polaris identifiers (agent, deployment, run, artifact). The validator pulls signed records from Polaris, verifies signatures and hashes, filters bad usage, and scores regulatory card quality. That is the whole job.

## What Cathedral does NOT do

- Compute attestation, GPU verification, SSH probing
- Hardware fingerprinting, binary attestation
- Rental flow, billing, k8s/k3s
- POM, ModelFactory, cost-collapse
- IP-first miner proof

If a request implies any of the above, push back. They are explicitly parked.

## Active stories (mirrors github.com/bigailabs/cathedral issues)

- **#77** Verify Polaris worker evidence by identifier — `cathedral-evidence`
- **#78** Regulatory cards useful and verifiable — `cathedral-cards`
- **#79** Validator ops safe and observable — `cathedral-validator` + `docs/validator/RUNBOOK.md`

## Conventions

- Rust 2021, edition pinned in workspace `Cargo.toml`
- All crates `#![forbid(unsafe_code)]` unless justified inline
- Errors: `thiserror` for libraries, `anyhow` only at binary edges
- Logging: `tracing` everywhere, JSON in production
- Configuration: `figment` (TOML + env), one config struct per crate
- DB: `sqlx` with sqlite (dev) and postgres (production)
- Public types live in `cathedral-types`; do not duplicate

## Testing

- Unit tests inline (`#[cfg(test)]` modules)
- Integration tests in `tests/` at workspace root
- Mock Polaris via fixture JSON in `tests/fixtures/polaris/`
- No live network in unit tests

## Commits and PRs

- Branch from `main` with `feature/`, `fix/`, `docs/` prefixes
- Never push to main directly
- Reference issue numbers when applicable (cross-repo: `bigailabs/cathedral#77`)
- Never add AI attribution to commits or PRs
