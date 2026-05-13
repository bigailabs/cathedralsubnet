#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="/opt/cathedral/source"
INSTALL_PREFIX="/opt/cathedral"
ALLOWED_SIGNERS="${INSTALL_PREFIX}/allowed_signers"
TAG_PREFIX="v"
POLL_SECS=600

# verify_tag <tag>
#
# Verifies a signed git tag using the SSH allowed-signers file at
# /opt/cathedral/allowed_signers. Returns 0 if the signature is valid,
# non-zero otherwise. Logs git's stderr so operators can diagnose failures
# (missing allowed_signers file, unknown signer, untrusted key, etc.).
#
# We rely on `git tag -v` exit code, not a substring of its output: the
# previous implementation grepped for "Good signature" which is the
# GPG-specific phrasing. SSH-signed tags use different output and the grep
# never matched, so the fleet never auto-updated.
verify_tag() {
  local tag="$1"
  local output rc=0
  # Capture combined stdout+stderr in `output`; check `git tag -v` exit code.
  # SSH and GPG produce different "good signature" phrasing, so we do not
  # grep -- we trust the exit code (0 = valid signature + principal matched,
  # non-zero = bad signature, untrusted signer, or missing allowed_signers).
  output=$(git -c "gpg.ssh.allowedSignersFile=${ALLOWED_SIGNERS}" \
    tag -v "$tag" 2>&1) || rc=$?
  if [[ "$rc" -eq 0 ]]; then
    return 0
  fi
  echo "$(date -u +%FT%TZ) updater: verify failed for $tag (exit=$rc): $output"
  return "$rc"
}

cd "$REPO_DIR"

while true; do
  git fetch --tags --quiet origin || { sleep "$POLL_SECS"; continue; }

  current=$(git describe --tags --exact-match HEAD 2>/dev/null || echo "none")
  latest=$(git tag -l "${TAG_PREFIX}*" --sort=-version:refname | head -1)

  if [[ -n "$latest" && "$current" != "$latest" ]]; then
    echo "$(date -u +%FT%TZ) updater: current=$current latest=$latest - verifying signature"

    if ! verify_tag "$latest"; then
      echo "$(date -u +%FT%TZ) updater: bad signature on $latest - refusing to update"
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
