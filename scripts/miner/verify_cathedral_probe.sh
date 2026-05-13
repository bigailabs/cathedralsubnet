#!/usr/bin/env bash
# Run ON the miner host AS the same user you put in ssh_user (e.g. cathedral-probe).
# Mirrors what Cathedral's SshHermesRunner does first: PATH, ~/.hermes, hermes profile create --clone-all.
#
# Example (replace with your clone path; user must match submit ssh_user):
#   sudo -u cathedral-probe bash /home/you/Projects/cathedral/scripts/miner/verify_cathedral_probe.sh
#
# If: sudo: unknown user cathedral-probe — create the account on THIS host first, then install the
# Cathedral SSH pubkey and Hermes for that user (docs/miner/QUICKSTART.md §2–§3). Do not use the
# literal path /path/to/cathedral; use your real repo path.
#
# If bash says Permission denied when using sudo -u cathedral-probe bash ~/.../verify_*.sh:
#   another user cannot traverse your home (mode 750). Copy the script first, then run it:
#     sudo install -o root -m0755 /home/you/Projects/cathedral/scripts/miner/verify_cathedral_probe.sh /tmp/cathedral-verify-probe.sh
#     sudo -u cathedral-probe bash /tmp/cathedral-verify-probe.sh
#
# Fix failures before resubmitting a NEW bundle (change zip bytes or Cathedral returns 409 duplicate).
set -euo pipefail

ok() { printf '\033[32mOK\033[0m %s\n' "$*"; }
fail() { printf '\033[31mFAIL\033[0m %s\n' "$*" >&2; exit 1; }
warn() { printf '\033[33mWARN\033[0m %s\n' "$*" >&2; }

echo "Prerequisites (read if a command above failed):"
echo "  • Run here: the host that equals ssh_host in your submit (Cathedral dials this:22)."
echo "  • Run as:  the Linux user that equals ssh_user (often cathedral-probe)."
echo "  • Missing user:  sudo useradd -m -s /bin/bash cathedral-probe"
echo "  • Missing Hermes: install on THIS host (Hermes agent docs)."
echo "  • Baseline repo: clone under ~/Projects/cathedral-baseline-agent or similar."
echo "  • sudo -u cathedral-probe … Permission denied: copy script out of a private home dir:"
echo "      sudo install -o root -m0755 /path/to/verify_cathedral_probe.sh /tmp/cathedral-verify-probe.sh"
echo "      sudo -u cathedral-probe bash /tmp/cathedral-verify-probe.sh"
echo ""

echo "== Cathedral probe self-check =="
echo "user: $(id -un) uid=$(id -u)"
echo "HOME=$HOME"

echo ""
echo "== 1) hermes on PATH (plain shell, like asyncssh non-login) =="
# Many distros give non-interactive SSH a tiny PATH (e.g. /usr/bin:/bin). Debian/Ubuntu often
# include /usr/local/bin — Kali may not. Test minimal first, then current $PATH.
_min_path="/usr/local/bin:/usr/bin:/bin"
if PATH="$_min_path" command -v hermes >/dev/null 2>&1; then
  ok "hermes on minimal PATH -> $(PATH="$_min_path" command -v hermes)"
  PATH="$_min_path" hermes --version
elif command -v hermes >/dev/null 2>&1; then
  ok "hermes -> $(command -v hermes) (not on minimal PATH — Cathedral may still fail; symlink to /usr/local/bin and ensure sshd PATH includes it)"
  hermes --version
elif [[ -x /usr/local/bin/hermes ]]; then
  fail "hermes is at /usr/local/bin/hermes but /usr/local/bin is not on minimal PATH. Re-run install_cathedral_probe_host.sh (symlinks + .profile), or: sudo ln -sf \"\$HOME/.local/bin/hermes\" /usr/local/bin/hermes"
else
  fail "hermes not found. Install Hermes, then run install_cathedral_probe_host.sh or: sudo ln -sf \"\$HOME/.local/bin/hermes\" /usr/local/bin/hermes"
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
