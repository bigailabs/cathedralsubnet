# Cathedral Subnet — Agent Notes

## Scope

This repo is the Cathedral Bittensor subnet validator + miner — a fresh implementation in Python scoped to evidence verification.

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

- **#2** Verify Polaris worker evidence by identifier — `cathedral.evidence`, `cathedral.validator.worker`
- **#3** Regulatory cards useful and verifiable — `cathedral.cards`
- **#1** Validator ops safe and observable — `cathedral.validator.{auth,health,stall}` + `docs/validator/RUNBOOK.md`

## Conventions

- Python 3.11+
- Pydantic v2 for all wire types
- Errors: domain-specific exception classes from each module's `__init__`
- Logging: `structlog` JSON in production, console renderer in dev
- Configuration: pydantic-settings (TOML + `CATHEDRAL_` env, nested `__`)
- DB: aiosqlite with WAL; single writer (the worker), readers tolerated
- Public types live in `cathedral.types`; do not duplicate

## Testing

- pytest with `pytest-asyncio` (auto mode)
- StubFetcher in `tests/conftest.py` simulates Polaris with a real Ed25519 keypair
- Integration tests use FastAPI's `TestClient`

## Lint, format, type

- `ruff check src tests` — must pass
- `ruff format src tests` — must be clean
- `mypy --strict` (config in pyproject.toml) — must pass

## Commits and PRs

- Branch from `main` with `feature/`, `fix/`, `docs/` prefixes
- Never push to main directly
- Reference issue numbers when applicable (cross-repo: `bigailabs/cathedral#2`)
- Never add AI attribution to commits or PRs
