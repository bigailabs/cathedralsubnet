#!/usr/bin/env bash
# One-shot miner probe setup (run ON the machine that is ssh_host in your submit).
#
#   sudo bash /home/you/Projects/cathedral/scripts/miner/install_cathedral_probe_host.sh
#
# Requires: root via sudo, curl, outbound HTTPS. You will be prompted for your sudo password once.
# Does NOT set LLM API keys — add /home/cathedral-probe/.hermes/.env yourself (Chutes/OpenRouter/etc.)
# or Cathedral eval will fail later with prompt_error.
set -euo pipefail

PROBE_USER="${PROBE_USER:-cathedral-probe}"
PUB_URL="https://api.cathedral.computer/.well-known/cathedral-ssh-key.pub"
HERMES_INSTALL_URL="https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh"

if [[ "${EUID:-0}" -ne 0 ]]; then
  echo "Run as root, e.g.:  sudo bash $0"
  exit 1
fi

if ! id "$PROBE_USER" &>/dev/null; then
  echo "==> creating user $PROBE_USER"
  useradd -m -s /bin/bash "$PROBE_USER"
fi

PROBE_HOME="$(getent passwd "$PROBE_USER" | cut -d: -f6)"
install -d -o "$PROBE_USER" -g "$PROBE_USER" -m 0700 "$PROBE_HOME/.ssh"

echo "==> installing Cathedral SSH public key for $PROBE_USER"
KEY_LINE=$(curl -fsSL "$PUB_URL" | tr -d '\r')
if [[ -z "$KEY_LINE" ]]; then
  echo "failed to fetch $PUB_URL" >&2
  exit 1
fi
AUTH_KEYS="$PROBE_HOME/.ssh/authorized_keys"
if [[ -f "$AUTH_KEYS" ]] && grep -qF "$KEY_LINE" "$AUTH_KEYS" 2>/dev/null; then
  echo "    key already present"
else
  printf '%s\n' "$KEY_LINE" >>"$AUTH_KEYS"
  chown "$PROBE_USER:$PROBE_USER" "$AUTH_KEYS"
  chmod 600 "$AUTH_KEYS"
fi
chmod 700 "$PROBE_HOME/.ssh"

echo "==> installing Hermes (non-interactive, wizard skipped) as $PROBE_USER"
sudo -u "$PROBE_USER" -H bash -lc "curl -fsSL '$HERMES_INSTALL_URL' | bash -s -- --skip-setup"

PROFILE_LINE='export PATH="$HOME/.local/bin:$PATH"'
PROFILE_FILE="$PROBE_HOME/.profile"
if [[ -f "$PROFILE_FILE" ]] && grep -qF '.local/bin' "$PROFILE_FILE" 2>/dev/null; then
  echo "==> PATH already mentions .local/bin in .profile"
else
  echo "==> appending PATH for ~/.local/bin to .profile"
  printf '\n# Cathedral probe: Hermes CLI\n%s\n' "$PROFILE_LINE" >>"$PROFILE_FILE"
  chown "$PROBE_USER:$PROBE_USER" "$PROFILE_FILE"
fi

echo "==> smoke: hermes --version as $PROBE_USER"
sudo -u "$PROBE_USER" -H bash -lc 'export PATH="$HOME/.local/bin:$PATH"; command -v hermes; hermes --version'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VERIFY_SRC="$SCRIPT_DIR/verify_cathedral_probe.sh"
if [[ -x "$VERIFY_SRC" ]]; then
  install -o root -m0755 "$VERIFY_SRC" /tmp/cathedral-verify-probe.sh
  echo "==> full probe check"
  sudo -u "$PROBE_USER" -H bash /tmp/cathedral-verify-probe.sh
else
  echo "WARN: missing $VERIFY_SRC — run verify manually"
fi

echo ""
echo "Done. Configure inference for evals:"
echo "  sudo -u $PROBE_USER -H nano $PROBE_HOME/.hermes/.env   # e.g. CHUTES_API_KEY / OPENROUTER_API_KEY"
echo "Then repack + submit a NEW bundle from your dev machine."
