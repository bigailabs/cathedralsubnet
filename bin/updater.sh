#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="/opt/cathedral/source"
INSTALL_PREFIX="/opt/cathedral"
TAG_PREFIX="v"
POLL_SECS=600

cd "$REPO_DIR"

while true; do
  git fetch --tags --quiet origin || { sleep "$POLL_SECS"; continue; }

  current=$(git describe --tags --exact-match HEAD 2>/dev/null || echo "none")
  latest=$(git tag -l "${TAG_PREFIX}*" --sort=-version:refname | head -1)

  if [[ -n "$latest" && "$current" != "$latest" ]]; then
    echo "$(date -u +%FT%TZ) updater: current=$current latest=$latest — verifying signature"

    if ! git tag -v "$latest" 2>&1 | grep -q "Good signature"; then
      echo "$(date -u +%FT%TZ) updater: bad signature on $latest — refusing to update"
      sleep "$POLL_SECS"
      continue
    fi

    echo "$(date -u +%FT%TZ) updater: checkout $latest + reinstall"
    git checkout --quiet "$latest"
    "$INSTALL_PREFIX/.venv/bin/pip" install --quiet -e .

    echo "$(date -u +%FT%TZ) updater: restart validator"
    pm2 reload cathedral-validator
  fi

  sleep "$POLL_SECS"
done
