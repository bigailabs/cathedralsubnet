// PM2 ecosystem for cathedral-validator + cathedral-updater.
//
// PM2 does NOT honor an `env_file` field (it's silently ignored). We parse
// /etc/cathedral/validator.env ourselves at config load and merge it into the
// per-app `env` object. Format: KEY=VALUE per line, # comments allowed,
// empty lines ignored, no quotes / no shell expansion.

const fs = require("fs");

function loadEnvFile(path) {
  if (!fs.existsSync(path)) return {};
  const out = {};
  for (const line of fs.readFileSync(path, "utf8").split("\n")) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;
    const eq = trimmed.indexOf("=");
    if (eq < 0) continue;
    out[trimmed.slice(0, eq)] = trimmed.slice(eq + 1);
  }
  return out;
}

const validatorEnv = loadEnvFile("/etc/cathedral/validator.env");

module.exports = {
  apps: [
    {
      name: "cathedral-validator",
      cwd: "/opt/cathedral/source",
      script: "/opt/cathedral/.venv/bin/cathedral-validator",
      args: "serve --config /etc/cathedral/testnet.toml --no-json-logs",
      interpreter: "none",
      env: validatorEnv,
      autorestart: true,
      max_restarts: 50,
      restart_delay: 10000,
      max_memory_restart: "1G",
      out_file: "/var/log/cathedral/validator.out.log",
      error_file: "/var/log/cathedral/validator.err.log",
      time: true,
    },
    {
      name: "cathedral-updater",
      cwd: "/opt/cathedral",
      script: "/opt/cathedral/source/bin/updater.sh",
      interpreter: "/bin/bash",
      autorestart: true,
      restart_delay: 60000,
      out_file: "/var/log/cathedral/updater.out.log",
      error_file: "/var/log/cathedral/updater.err.log",
      time: true,
    },
  ],
};
