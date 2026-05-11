#!/usr/bin/env bash
# Provision a Polaris CPU box as a long-lived Cathedral miner-probe.
#
# Stands up the cathedral-runtime container under PM2 with the miner hotkey
# mounted in and traces persisted to /var/lib/cathedral-probe/traces. The
# probe endpoints (/probe/run, /probe/health, /probe/reload) are added by
# Stage B.1 of BUILD_V1.md — this script just gets the container running so
# those endpoints come online as soon as a runtime tag containing them is
# pulled.
#
# Idempotent — safe to re-run after a tag bump or hotkey rotation.
set -euo pipefail

# --- Inputs ------------------------------------------------------------------

: "${CATHEDRAL_RUNTIME_TAG:=v1.0.7}"
: "${CATHEDRAL_PROBE_PORT:=8088}"
: "${MINER_HOTKEY_JSON_PATH:?MINER_HOTKEY_JSON_PATH is required (path to unencrypted hotkey JSON on the host)}"
: "${PROBE_PUBLIC_HOSTNAME:?PROBE_PUBLIC_HOSTNAME is required (externally reachable hostname for the probe; used by the publisher when routing /probe/run)}"

PROBE_USER="cathedral-probe"
PROBE_HOME="/home/${PROBE_USER}"
PROBE_OPT="/opt/cathedral-probe"
PROBE_ETC="/etc/cathedral-probe"
PROBE_TRACES_DIR="/var/lib/cathedral-probe/traces"
PROBE_LOG_DIR="/var/log/cathedral-probe"
PROBE_HOTKEY_DST="${PROBE_ETC}/hotkey.json"
PROBE_ECOSYSTEM="${PROBE_OPT}/ecosystem.config.cjs"
PROBE_ENV_FILE="${PROBE_ETC}/probe.env"
RUNTIME_IMAGE="ghcr.io/cathedralai/cathedral-runtime:${CATHEDRAL_RUNTIME_TAG}"

if [[ ! -f "${MINER_HOTKEY_JSON_PATH}" ]]; then
  echo "MINER_HOTKEY_JSON_PATH does not point at a readable file: ${MINER_HOTKEY_JSON_PATH}" >&2
  exit 1
fi

echo "==> step 1: install docker + nodejs + npm if missing"
NEED_APT_UPDATE=0
need_pkg() {
  # dpkg-query exits non-zero when the package isn't installed; cheaper than
  # forcing `apt-get install` every run.
  if ! dpkg-query -W -f='${Status}' "$1" 2>/dev/null | grep -q "install ok installed"; then
    return 0
  fi
  return 1
}

MISSING_PKGS=()
for pkg in docker.io nodejs npm; do
  if need_pkg "${pkg}"; then
    MISSING_PKGS+=("${pkg}")
  fi
done

if (( ${#MISSING_PKGS[@]} > 0 )); then
  NEED_APT_UPDATE=1
  sudo apt-get update
  sudo apt-get install -y "${MISSING_PKGS[@]}"
else
  echo "    docker.io, nodejs, npm already installed — skipping apt"
fi

echo "==> step 2: ensure ${PROBE_USER} user exists"
if ! id "${PROBE_USER}" >/dev/null 2>&1; then
  sudo useradd -m -s /bin/bash "${PROBE_USER}"
else
  echo "    user ${PROBE_USER} already exists — skipping"
fi

# docker group lets the probe user run `docker run` without sudo at PM2 time.
if getent group docker >/dev/null 2>&1; then
  if ! id -nG "${PROBE_USER}" | tr ' ' '\n' | grep -qx docker; then
    sudo usermod -aG docker "${PROBE_USER}"
  fi
fi

echo "==> step 3: create state directories"
sudo install -d -o "${PROBE_USER}" -g "${PROBE_USER}" -m 0755 \
  "${PROBE_OPT}" \
  "${PROBE_ETC}" \
  "${PROBE_TRACES_DIR}" \
  "${PROBE_LOG_DIR}"
# /var/lib/cathedral-probe is the parent of traces; install -d above only
# guarantees the leaf, so chown the parent too if it exists.
if [[ -d /var/lib/cathedral-probe ]]; then
  sudo chown "${PROBE_USER}:${PROBE_USER}" /var/lib/cathedral-probe
fi

echo "==> step 4: copy miner hotkey to ${PROBE_HOTKEY_DST}"
sudo install -o "${PROBE_USER}" -g "${PROBE_USER}" -m 0600 \
  "${MINER_HOTKEY_JSON_PATH}" "${PROBE_HOTKEY_DST}"

echo "==> step 5: docker pull ${RUNTIME_IMAGE}"
sudo docker pull "${RUNTIME_IMAGE}"

echo "==> step 6: write ${PROBE_ECOSYSTEM}"
# PM2 reads process.env at start time, so we write the ecosystem in a
# tag-agnostic way and rely on /etc/cathedral-probe/probe.env sourced before
# `pm2 start` to pin CATHEDRAL_RUNTIME_TAG + CATHEDRAL_PROBE_PORT for this
# host. That way a future updater can rewrite probe.env and `pm2 reload`
# without rewriting the ecosystem file.
sudo tee "${PROBE_ECOSYSTEM}" >/dev/null <<'EOF'
module.exports = {
  apps: [{
    name: "cathedral-probe",
    script: "docker",
    args: [
      "run", "--rm", "--name", "cathedral-probe",
      "-p", `${process.env.CATHEDRAL_PROBE_PORT || 8088}:8088`,
      "-v", "/etc/cathedral-probe/hotkey.json:/etc/cathedral-probe/hotkey.json:ro",
      "-v", "/var/lib/cathedral-probe/traces:/var/lib/cathedral-probe/traces",
      "-e", "CATHEDRAL_PROBE_MODE=true",
      "ghcr.io/cathedralai/cathedral-runtime:" + (process.env.CATHEDRAL_RUNTIME_TAG || "v1.0.7"),
    ],
    interpreter: "none",
    autorestart: true,
    max_restarts: 50,
    restart_delay: 10000,
    out_file: "/var/log/cathedral-probe/probe.out.log",
    error_file: "/var/log/cathedral-probe/probe.err.log",
    time: true,
  }],
};
EOF
sudo chown "${PROBE_USER}:${PROBE_USER}" "${PROBE_ECOSYSTEM}"

echo "==> step 7: write ${PROBE_ENV_FILE}"
sudo tee "${PROBE_ENV_FILE}" >/dev/null <<EOF
CATHEDRAL_RUNTIME_TAG=${CATHEDRAL_RUNTIME_TAG}
CATHEDRAL_PROBE_PORT=${CATHEDRAL_PROBE_PORT}
EOF
sudo chown "${PROBE_USER}:${PROBE_USER}" "${PROBE_ENV_FILE}"
sudo chmod 0640 "${PROBE_ENV_FILE}"

echo "==> step 8: install pm2 globally if missing"
if ! command -v pm2 >/dev/null 2>&1; then
  sudo npm install -g pm2
else
  echo "    pm2 already installed — skipping"
fi

echo "==> step 9: start the probe under pm2 as ${PROBE_USER}"
# pm2 does not have a --env-file flag, so we source probe.env into the
# subshell that runs `pm2 start`. PM2 captures the process env at start
# time, which is exactly what the ecosystem's process.env reads expect.
# If the app already exists, restart it so config changes take effect.
if sudo -u "${PROBE_USER}" -H pm2 list 2>/dev/null | grep -q "cathedral-probe"; then
  sudo -u "${PROBE_USER}" -H bash -c "set -a; . '${PROBE_ENV_FILE}'; set +a; pm2 restart '${PROBE_ECOSYSTEM}' --update-env"
else
  sudo -u "${PROBE_USER}" -H bash -c "set -a; . '${PROBE_ENV_FILE}'; set +a; pm2 start '${PROBE_ECOSYSTEM}'"
fi

echo "==> step 10: pm2 save"
sudo -u "${PROBE_USER}" -H pm2 save

echo "==> step 11: pm2 startup (idempotent)"
# `pm2 startup` is idempotent — if the systemd unit is already installed it
# prints "already inited" and exits 0. Run it unconditionally so a
# freshly-provisioned box always gets boot persistence.
sudo env "PATH=${PATH}:/usr/bin" pm2 startup systemd -u "${PROBE_USER}" --hp "${PROBE_HOME}"

cat <<EOF

==> probe running at ${PROBE_PUBLIC_HOSTNAME}:${CATHEDRAL_PROBE_PORT}/probe/health

Note: the /probe/run, /probe/health, and /probe/reload endpoints are added
in Stage B.1 of BUILD_V1.md. Until a runtime image containing those routes
is pulled, /probe/health will 404 — that is expected and not a sign of
broken provisioning.

To verify supervision:
    sudo -u ${PROBE_USER} pm2 status
    sudo -u ${PROBE_USER} pm2 logs cathedral-probe --lines 100

To bump the runtime tag:
    sudo sed -i 's/^CATHEDRAL_RUNTIME_TAG=.*/CATHEDRAL_RUNTIME_TAG=v1.0.X/' ${PROBE_ENV_FILE}
    sudo docker pull ghcr.io/cathedralai/cathedral-runtime:v1.0.X
    sudo -u ${PROBE_USER} -H bash -c "set -a; . '${PROBE_ENV_FILE}'; set +a; pm2 restart cathedral-probe --update-env"
EOF
