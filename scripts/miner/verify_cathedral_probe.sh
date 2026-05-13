#!/usr/bin/env bash
# Run ON the miner host AS the same user you put in ssh_user (e.g. cathedral-probe).
# Mirrors what Cathedral's SshHermesRunner does first: PATH, ~/.hermes, hermes profile create --clone-all.
#
#   sudo -u cathedral-probe bash /path/to/cathedral/scripts/miner/verify_cathedral_probe.sh
#
# Fix failures before resubmitting a NEW bundle (change zip bytes or Cathedral returns 409 duplicate).
set -euo pipefail

ok() { printf '\033[32mOK\033[0m %s\n' "$*"; }
fail() { printf '\033[31mFAIL\033[0m %s\n' "$*" >&2; exit 1; }
warn() { printf '\033[33mWARN\033[0m %s\n' "$*" >&2; }

echo "== Cathedral probe self-check =="
echo "user: $(id -un) uid=$(id -u)"
echo "HOME=$HOME"

echo ""
echo "== 1) hermes on PATH (plain shell, like asyncssh non-login) =="
if command -v hermes >/dev/null 2>&1; then
  ok "hermes -> $(command -v hermes)"
  hermes --version
else
  fail "hermes not on PATH. Install Hermes and add it to PATH for non-interactive sessions (e.g. ~/.profile, /etc/environment, or symlink into /usr/local/bin)."
fi

echo ""
echo "== 2) hermes via bash -lc (login-style PATH) =="
if bash -lc 'command -v hermes >/dev/null'; then
  ok "hermes visible under bash -lc"
  bash -lc 'hermes --version'
else
  warn "hermes missing under bash -lc — fix PATH in ~/.bash_profile / ~/.profile"
fi

HERMES_HOME_DIR="${HERMES_HOME_DIR:-$HOME/.hermes}"
echo ""
echo "== 3) Hermes data dir readable (default ~/.hermes) =="
if [[ ! -d "$HERMES_HOME_DIR" ]]; then
  fail "Missing directory: $HERMES_HOME_DIR (create via Hermes or copy a profile here)"
fi
if [[ ! -r "$HERMES_HOME_DIR" ]]; then
  fail "Not readable: $HERMES_HOME_DIR"
fi
ok "$HERMES_HOME_DIR exists"
ls -la "$HERMES_HOME_DIR" 2>/dev/null | head -15 || true

echo ""
echo "== 4) profiles dir (clone-all needs a source profile) =="
if [[ ! -d "$HERMES_HOME_DIR/profiles" ]]; then
  warn "No profiles/ yet — run: hermes profile install … from cathedral-baseline-agent (see docs/miner/QUICKSTART.md)"
else
  ls -la "$HERMES_HOME_DIR/profiles" | head -20 || true
fi

echo ""
echo "== 5) clone-all smoke (same as Cathedral eval prep) =="
TEST_PROFILE="cathedral-probe-selfcheck-$$"
if hermes profile create "$TEST_PROFILE" --clone-all; then
  ok "hermes profile create $TEST_PROFILE --clone-all"
else
  fail "hermes profile create --clone-all failed — fix default/active profile first (hermes profile list / install baseline)"
fi
if hermes profile delete "$TEST_PROFILE" --yes; then
  ok "removed $TEST_PROFILE"
else
  warn "could not delete $TEST_PROFILE — delete manually"
fi

echo ""
echo "== 6) /tmp writable (state.db snapshot during eval) =="
if [[ -w /tmp ]]; then
  ok "/tmp writable ($(df -h /tmp | tail -1))"
else
  fail "/tmp not writable"
fi

echo ""
echo "== 7) authorized_keys present =="
if [[ -f "$HOME/.ssh/authorized_keys" ]]; then
  lines=$(wc -l <"$HOME/.ssh/authorized_keys" | tr -d ' ')
  ok "~/.ssh/authorized_keys ($lines line(s))"
  pub_url="https://api.cathedral.computer/.well-known/cathedral-ssh-key.pub"
  if command -v curl >/dev/null 2>&1; then
    pub_blob=$(curl -fsSL "$pub_url" | awk '{print $2}')
    if [[ -n "$pub_blob" ]] && grep -qF "$pub_blob" "$HOME/.ssh/authorized_keys" 2>/dev/null; then
      ok "Cathedral probe key material found in authorized_keys"
    else
      warn "Could not confirm Cathedral key blob — reinstall from: $pub_url"
    fi
  else
    warn "curl not installed; verify key manually against $pub_url"
  fi
else
  fail "Missing $HOME/.ssh/authorized_keys — install Cathedral pubkey (docs/miner/QUICKSTART.md §3)"
fi

echo ""
echo "All automated checks passed. From another network, test: ssh -p 22 ${USER}@$(curl -fsSL https://api.ipify.org 2>/dev/null || echo YOUR_PUBLIC_IP)"
echo "Then repack + submit a NEW zip so eval runs again."
