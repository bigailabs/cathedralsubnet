#!/usr/bin/env bash
# Pack cathedral-baseline-agent (+ eval-spec) into a zip suitable for
# POST /v1/agents/submit. Requires eval-spec at ./eval-spec (symlink OK).
set -euo pipefail

BASELINE_DIR="${BASELINE_DIR:-${HOME}/Projects/cathedral-baseline-agent}"
OUT_ZIP="${OUT_ZIP:-${BASELINE_DIR}/cathedral-baseline-bundle.zip}"

if [[ ! -d "${BASELINE_DIR}" ]]; then
  echo "BASELINE_DIR not found: ${BASELINE_DIR}" >&2
  exit 1
fi
if [[ ! -d "${BASELINE_DIR}/eval-spec" ]]; then
  echo "Missing ${BASELINE_DIR}/eval-spec — clone cathedral-eval-spec there or symlink." >&2
  exit 1
fi

cd "${BASELINE_DIR}"
rm -f "${OUT_ZIP}"
zip -r "${OUT_ZIP}" \
  soul.md AGENTS.md config.yaml mcp_servers.yaml card_schema.json \
  cron skills \
  eval-spec \
  -x 'eval-spec/.git/*' -x '*/.git/*' -x '*.pyc' -x '__pycache__/*'

echo "Wrote ${OUT_ZIP} ($(du -h "${OUT_ZIP}" | cut -f1))"
