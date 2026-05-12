# Hermes Agent — Technical Reference Dossier for Cathedral

Version covered: **Hermes Agent v0.13.0** (HEAD = `38441a7d`, commit dated 2026-05-12, repo `github.com/NousResearch/hermes-agent`). All docs URLs are under `https://hermes-agent.nousresearch.com/docs/`. Where docs were thin I cite the source file path inside the cloned repo at `/tmp/hermes-agent/`.

The single biggest correction up front: **Hermes does not have a /chat HTTP endpoint, and Cathedral's `cathedral-runtime` wrapper was solving a problem Hermes already solves better natively.** The canonical programmatic entry is `hermes -z "<prompt>"` (oneshot.py), which writes the full agent state to the same SQLite + JSON artifacts a normal session writes. We just have to read those artifacts.

---

## A. Driving Hermes programmatically over SSH

### A.1 `hermes -z "<prompt>"` is the canonical one-shot
Source: `/tmp/hermes-agent/hermes_cli/oneshot.py`, lines 1–200; `_parser.py` line 100 (`"-z", "--oneshot"`); docs at `/docs/reference/cli-commands` § "hermes -z <prompt> — Scripted One-Shot".

- **Output shape:** plain text only, written to real stdout once the agent's `chat()` returns. Everything else (banner, spinner, tool previews, session ID line, logging) is suppressed via `redirect_stdout(devnull)` + `redirect_stderr(devnull)` + `logging.disable(CRITICAL)`. There is **no JSON output mode for `-z`**. (oneshot.py:174-198)
- **Not streaming.** `agent.stream_delta_callback = None` is explicitly set (oneshot.py:333). The function blocks until the agent's tool loop terminates, then prints once.
- **Same agent loop as `hermes chat`.** `-z` builds the same `AIAgent` (`run_agent.py`), loads `AGENTS.md`, `SOUL.md`, `.cursorrules`, memory, and the user's configured "cli" toolsets unless `--toolsets` is given. (oneshot.py:8, 230-310)
- **Same SQLite writes.** `-z` instantiates `SessionDB()` (oneshot.py:202-215) so a row is added to `~/.hermes/state.db` `sessions` table and every turn lands in the `messages` table. **This is the key property for Cathedral: a `-z` invocation leaves the same forensic trail as an interactive chat.**
- **Auto-approves dangerous commands** via `HERMES_YOLO_MODE=1` and `HERMES_ACCEPT_HOOKS=1` (oneshot.py:171-172). For Cathedral evals this is correct — nothing can hang. Operationally, the miner box has to be sandboxed because the agent will do whatever the task asks.
- **Model/provider override:** `--model` / `-m`, `--provider`, plus env vars `HERMES_INFERENCE_MODEL` and `HERMES_INFERENCE_PROVIDER`. `--provider` without `--model` returns exit 2 (oneshot.py:155-161). Cathedral can pin the provider+model per eval to neutralize that variable.

### A.2 `hermes chat -q "<prompt>"` is distinct from `-z`
Source: `/docs/reference/cli-commands` § "hermes chat", `_parser.py` line 240.

- `chat -q` runs through the **full** chat code path (cli.py), which prints a banner, a session ID line, and tool-progress chatter unless you also pass `-Q/--quiet`. Even with `-Q`, the surrounding `cli.py` lifecycle is heavier than `-z`. Use `-z` for Cathedral, not `chat -q`.
- `chat` accepts `--image <path>`, `--source <tag>` (session source tag, default `cli`), `--max-turns <N>` — Cathedral can tag every eval invocation with `--source cathedral-eval-<round>` and later filter by that source in the SQLite store.

### A.3 Structured input/output
- **Structured input:** no native JSON task spec — the prompt is a single string. System prompt override is via `SOUL.md` and `AGENTS.md` (file based), not flags. Skill preload list is `-s skill1 -s skill2` or `--skills skill1,skill2` (`_parser.py`:177-184).
- **Structured output:** none provided by `-z`. If Cathedral needs JSON, it has to instruct the agent to emit JSON inside the prompt (and validate). The **real** structured artifact is the post-run session log on disk (see Section B).

### A.4 `hermes mcp serve` — what it actually is
Source: `/tmp/hermes-agent/mcp_serve.py:1-28, 866-890`; docs at `/docs/user-guide/features/mcp`.

- `hermes mcp serve` runs Hermes **as an MCP server over stdio**. There is **no HTTP/SSE transport** in v0.13.0 — confirmed by `await server.run_stdio_async()` in mcp_serve.py line 890.
- **This server does not expose "send a prompt to the agent."** It exposes 10 tools — `conversations_list`, `conversation_get`, `messages_read`, `attachments_fetch`, `events_poll`, `events_wait`, `messages_send`, `channels_list`, `permissions_list_open`, `permissions_respond` — that are scoped to driving the **messaging gateway** (Telegram/Discord/Slack/etc.). It's an inbox-and-outbox MCP, not a chat completion MCP.
- **Therefore `hermes mcp serve` is NOT the right driver for Cathedral.** It can't be used to submit a prompt and get an answer. It's only useful if Cathedral wanted to observe the agent's messaging activity after the fact.
- **Driving Hermes via MCP from another box (SSH stdio tunnel)** would require wrapping `ssh miner-host hermes -z "<prompt>"` inside an MCP shim Cathedral writes itself. The native MCP server doesn't offer this.

### A.5 Tool-call / reasoning / model-call traces
Source: `run_agent.py:5196-5260` (`_save_session_log`), `hermes_state.py:224-240` (messages table), `run_agent.py:5135-5180` (`request_dump`).

- **Per-session JSON log** at `~/.hermes/sessions/session_<session_id>.json`, overwritten after each turn. Schema (run_agent.py:5238-5249):
  ```json
  {
    "session_id": "...",
    "model": "...",
    "base_url": "...",
    "platform": "cli",
    "session_start": "ISO-8601",
    "last_updated": "ISO-8601",
    "system_prompt": "<full assembled system prompt>",
    "tools": [<full OpenAI-format tool schemas>],
    "message_count": N,
    "messages": [
      {"role":"system|user|assistant|tool", "content":"...", "tool_calls":[...], "tool_call_id":"...", "tool_name":"...", "reasoning":"...", "reasoning_content":"...", "reasoning_details":[...], "finish_reason":"...", "timestamp":..., "token_count":...}
    ]
  }
  ```
- **Per-call request dumps:** `request_dump_<session_id>_<timestamp>.json` written into the same `sessions/` directory on every API call (run_agent.py:5168). Contains the full request body sent to the LLM provider, plus error info if the call failed. This is the lowest-level trace.
- **SQLite session messages table** (`hermes_state.py:224-240`) stores per-message: role, content, tool_call_id, tool_calls (JSON), tool_name, timestamp, token_count, finish_reason, **reasoning, reasoning_content, reasoning_details** (separate columns for OpenAI-style, alt-provider-style, and OpenRouter-unified-style reasoning traces), codex_reasoning_items, codex_message_items.
- **For Cathedral:** after a `-z` returns, Cathedral has three forensic surfaces it can hash and Merkle-anchor:
  1. The session JSON at `~/.hermes/sessions/session_<id>.json` — full conversation + tools + system prompt.
  2. Every `request_dump_<id>_*.json` — every API request/response pair.
  3. The corresponding `sessions` + `messages` rows in `state.db` — also include token accounting, cost, billing provider, parent_session_id, model_config.

---

## B. Capturing the agent's complete on-disk state

### B.1 Directory layout under `~/.hermes/`
Source: `/docs/user-guide/configuration` and `hermes_cli/profiles.py:36-65` and `hermes_cli/backup.py:34-66`.

```
~/.hermes/
├── config.yaml              # primary config (model, terminal, TTS, compression, ...)
├── .env                     # secrets (API keys, OAuth tokens)
├── auth.json                # OAuth provider credentials
├── SOUL.md                  # agent identity (user-owned, never auto-modified per docs)
├── state.db                 # SQLite: sessions, messages, FTS5 indices, schema_version, state_meta
├── state.db-wal, state.db-shm  # WAL sidecars (excluded from backup; reconstructed via sqlite3.backup())
├── kanban.db                # kanban tasks (multi-profile board)
├── memories/
│   ├── MEMORY.md            # agent self-notes, 2200 char cap (~800 tokens)
│   └── USER.md              # user profile, 1375 char cap (~500 tokens)
├── skills/                  # SKILL.md trees per skill (bundled + user-created + hub-installed)
│   └── <category>/<name>/SKILL.md + references/ + templates/ + scripts/ + assets/
├── sessions/
│   ├── sessions.json                            # gateway sessions index
│   ├── session_<session_id>.json                # full per-session log
│   ├── <session_id>.jsonl                       # gateway-side append-only log
│   └── request_dump_<session_id>_<ts>.json      # every raw LLM API request/response
├── cron/jobs.json           # scheduled jobs
├── logs/
│   ├── agent.log            # agent activity (API calls, tool dispatch, lifecycle)
│   ├── errors.log           # WARN+ subset
│   └── gateway.log          # gateway activity, platform connections, webhooks
├── checkpoints/             # shadow git store for file rollback (per-session trajectory caches)
├── workspace/               # default agent CWD
├── home/                    # per-profile HOME for subprocess credentials isolation
├── plans/                   # planning artifacts
├── skins/                   # CLI theming
├── channel_directory.json   # cached gateway channel directory
├── slack-manifest.json      # generated Slack app manifest if used
└── profiles/<name>/         # additional profiles, each a full HERMES_HOME tree
```

### B.2 What each "export" command actually does
Source: `hermes_cli/backup.py:34-200`, `hermes_state.py:2217-2235`, `hermes_cli/dump.py:1-100`, docs `/docs/reference/cli-commands` § hermes backup / dump / sessions / skills / memory / checkpoints / insights.

| Command | What it produces | Includes everything? |
|---|---|---|
| `hermes backup` | `~/hermes-backup-<ts>.zip` of nearly the whole `~/.hermes/` tree. Uses `sqlite3.backup()` for `state.db` and `kanban.db` to get a consistent snapshot. Excludes `hermes-agent/` (codebase), `__pycache__`, `node_modules`, `backups/`, `checkpoints/` (per-session caches), `*.pyc`, `*.db-wal/-shm/-journal`, `gateway.pid`, `cron.pid`. **`.env`, `auth.json`, `state.db` are included** but extraction tightens them to 0600. | Closest thing to "complete state snapshot" — but **omits `checkpoints/`** by design. |
| `hermes backup --quick` | Lightweight snapshot of config, state.db, .env, auth, cron only. | No |
| `hermes dump` | Plain-text setup summary (version, env, API key presence, providers, models, toolsets, MCP servers, gateway status, cron, skills count, config overrides). Designed for copy-paste in Discord, not for full state capture. | No — metadata only |
| `hermes sessions export <out> [--session-id ID]` | JSONL of sessions + messages from `state.db`. Per-session export shape: `{...session row..., "messages": [...]}` (`hermes_state.py:2217-2235`). | Sessions + messages from SQLite only — does not include `request_dump_*.json` files. |
| `hermes skills` (no native export-all) | `hermes skills snapshot` exports/imports skill **configurations** (per `/docs/reference/cli-commands`). For full skill contents the data is just `~/.hermes/skills/`, so `tar -czf skills.tgz ~/.hermes/skills/`. | Manual file copy |
| `hermes memory status` | Shows config of memory provider; doesn't export content. `MEMORY.md` and `USER.md` are flat files — copy directly. | Manual file copy |
| `hermes logs <name>` | Print/tail log file contents. No export, just read. | Filtered view |
| `hermes checkpoints status` | Size/project breakdown of shadow git store. To export: `tar -czf checkpoints.tgz ~/.hermes/checkpoints/`. | Manual file copy |
| `hermes insights` | Token/cost/activity analytics over the last N days. Read-only summary. | Derived analytics, not raw state |
| `hermes profile export <name>` | `tar.gz` of an entire named profile directory. Best single-shot if Cathedral spawns a per-eval profile. | Yes for that profile |

### B.3 The canonical "snapshot everything" procedure for Cathedral

There is **no single `hermes snapshot --everything-signed` command**. The minimum sequence to capture the complete state of a Hermes agent after an eval is:

1. `hermes -z "<task>" --source cathedral-eval-<round-id>` — runs the eval, returns plain-text answer to stdout. Stderr is silent.
2. Read the new SQLite `sessions` row + `messages` rows filtered by `source = "cathedral-eval-<round-id>"`. (Source column lives on `sessions` table — `hermes_state.py:192`.)
3. Read every file matching `~/.hermes/sessions/session_<session_id>.json` and `~/.hermes/sessions/request_dump_<session_id>_*.json` written since eval-start.
4. **Optionally** `hermes backup -o /tmp/agent-snapshot-<round>.zip` for the full-state weekly Merkle anchor (this is the right cadence — daily would be wasteful given the GB-scale checkpoint dir which `backup` correctly excludes).
5. Hash + sign the bundle (eval response + session log + request dumps + SQLite slice + backup zip) and submit to chain.

The weekly Merkle anchor naturally maps to `hermes backup` runs because the backup zip is the "full" state and the per-eval slices are the leaves.

---

## C. Configuration surface

Source: `/docs/user-guide/configuration` (full schema dump above in conversation log) and `cli-config.yaml.example` in the repo root.

### C.1 Highlights

- **Single config.yaml under HERMES_HOME** with precedence: CLI args > config.yaml > .env > built-in defaults.
- **Model selection:** `model.default`, `model.provider`, `model.base_url`, `model.context_length`, `model.timeout_seconds`. Per-provider overrides under `providers.<name>.*`.
- **Multi-provider per task:** Yes, partial — there are dedicated `auxiliary.vision`, `auxiliary.web_extract`, `auxiliary.approval`, `auxiliary.session_search`, `auxiliary.triage_specifier`, `auxiliary.compression` blocks, each with independent provider/model/base_url/api_key/timeout. The main chat invocation can override at call time with `--provider` and `--model`. There is no built-in "task-class routing" beyond these named auxiliary slots.
- **Fallback chain:** Configured via `hermes fallback add/remove/clear/list`. Stored in config under `fallback` (the docs are thin here — `hermes fallback list` is the operational source of truth).
- **Tools per platform:** `hermes tools` (or `hermes tools --summary`) — per-platform toolset enablement persisted in config.yaml. The "platform" key matters because the **same agent** behaves differently on CLI vs Telegram vs Slack, etc.
- **Profile isolation:** `hermes profile create <name>` creates `~/.hermes/profiles/<name>/` which is a complete independent HERMES_HOME (own config.yaml, .env, memories, sessions, skills, state.db). `hermes -p <name> chat ...` or `HERMES_HOME=...` env var selects it. Multiple profiles can run simultaneously **except** that messaging-platform bot tokens are exclusive — two gateways can't share one Telegram bot. (FAQ confirmed.)

### C.2 Env vars Cathedral cares about
`HERMES_INFERENCE_MODEL`, `HERMES_INFERENCE_PROVIDER` (override per call), `HERMES_HOME` (point to a named profile), `HERMES_YOLO_MODE=1` (skip approvals), `HERMES_ACCEPT_HOOKS=1` (auto-approve hooks), `HERMES_DUMP_REQUEST_STDOUT` (mirror request_dump to stdout — useful for live observation in Cathedral), `HERMES_INTERACTIVE` (never set by Cathedral — gates sudo prompts), provider keys (`OPENROUTER_API_KEY`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `CHUTES_API_KEY` if configured, etc.).

---

## D. Skills system in depth

Source: `/docs/user-guide/features/skills`, `tools/skill_manager_tool.py`, `tools/skill_provenance.py`, `tools/skills_sync.py`.

- **Storage:** `~/.hermes/skills/<category>/<name>/SKILL.md` plus optional `references/`, `templates/`, `scripts/`, `assets/` subdirs.
- **Format:** Markdown file with YAML frontmatter — `name`, `description`, `version`, `author`, `platforms` (macos/linux/windows), `category`, `fallback_for_toolsets`, `requires_toolsets`, `required_environment_variables`, plus markdown body sections (`When to Use`, `Procedure`, `Pitfalls`, `Verification`).
- **Self-improving loop:** the agent invokes the internal `skill_manage` tool (actions: `create`, `patch`, `edit`, `delete`, `write_file`, `remove_file`) during a session when it solves a complex task. `_iters_since_skill` counter and "5+ tool calls" heuristic from docs. The agent **literally writes a new SKILL.md** to `~/.hermes/skills/` based on what it just did. This is how Hermes "self-improves."
- **Curator:** `hermes curator run [--background|--dry-run]` runs an LLM pass over the skills tree to review, prune, consolidate, and archive. Snapshots before mutations into `~/.hermes/curator_backups/*.tar.gz`. `hermes curator pin <name>` prevents auto-transitions.
- **Versioning:** Each skill has a `version` field. Bundled skills carry `.bundled_manifest` to detect upstream drift. `hermes skills check / update / reset` manages drift.
- **Sharing:** `hermes skills publish` to a registry. `hermes skills snapshot` for export/import. `hermes skills tap add <owner/repo>` for org-private skill repos. Skills installed from the hub go through a security scanner.
- **For Cathedral:** the skills tree is part of the agent's "moat." A weekly snapshot of `~/.hermes/skills/` captures the agent's accumulated learned procedures. Diffing week N vs N+1 shows what the agent learned.

---

## E. Memory + sessions + insights

Source: `/docs/user-guide/features/memory`, `run_agent.py:1977-2010`, `hermes_state.py`.

- **Memory files:** `~/.hermes/memories/MEMORY.md` (2200 char), `~/.hermes/memories/USER.md` (1375 char). Loaded into the system prompt at session start as a **frozen snapshot** (good for prefix caching). The agent manages them via the `memory_tool` (actions: `add`, `replace`, `remove` — no `read`, because memory is auto-injected).
- **Memory nudges:** `_memory_nudge_interval = 10` (config: `memory.nudge_interval`). Every N user turns, the agent is nudged to consider persisting something to memory. (run_agent.py:1989, 11966-12004.)
- **External memory providers** (one at a time, alongside built-in): honcho, openviking, mem0, hindsight, holographic, retaindb, byterover, supermemory. Configured via `hermes memory setup`. Each may register a top-level `hermes <provider>` command (e.g., `hermes honcho`).
- **Sessions:** SQLite `state.db` table `sessions` carries the *metadata* (id, source, user_id, model, model_config, system_prompt, parent_session_id, started_at, ended_at, end_reason, message_count, tool_call_count, token counters by class, billing fields, title, api_call_count, handoff_state). Table `messages` carries the per-message records with reasoning columns. FTS5 indexes both `messages_fts` (unicode61) and `messages_fts_trigram` (for CJK). Lineage tracking via `parent_session_id` lets the system follow forks/compressions.
- **`hermes insights`** — `--days N --source platform`. Reports token/cost/activity. **Useful for Cathedral as a sanity-check oracle** during scoring: was the agent's run cost in line with the rubric's expected complexity?

---

## F. Gateway / messaging / webhooks

Source: `/docs/user-guide/messaging`, `gateway/` directory, `hermes_cli/webhook.py`.

- **`hermes gateway run`** runs a single background process that adapters into 20+ platforms (Telegram, Discord, Slack, WhatsApp, Signal, Matrix, Mattermost, Teams, WeCom, Feishu/Lark, DingTalk, email, Twilio SMS, BlueBubbles iMessage, QQ, LINE, Weixin, Home Assistant, Open WebUI, custom webhooks). Each adapter routes incoming messages through a per-chat session store into `AIAgent`.
- **`hermes gateway install`** installs as systemd (Linux) / launchd (macOS) service. State at `~/.hermes/gateway.pid` and `~/.hermes/gateway_state.json`. Logs to `~/.hermes/logs/gateway.log`.
- **Cron scheduler runs inside the gateway** — ticks every 60s, executes due jobs.
- **`hermes webhook subscribe`** creates a webhook route — returns a URL + HMAC secret. Delivery targets: `log`, `telegram`, `discord`, `slack`, `github_comment`. `--deliver-only` skips the LLM call and forwards a rendered template (zero LLM cost). The gateway hosts the webhook HTTP receiver. Specific port is configured in gateway config (docs don't pin it; check `gateway.http_port` in config.yaml — **unverified — needs source code read at `/tmp/hermes-agent/gateway/`**).
- **For Cathedral:** the gateway is **irrelevant** to the eval path. Cathedral evals should never touch the gateway because gateway sessions are tied to user identities and conversation continuity. Cathedral's traffic should hit `-z` directly with `--source cathedral-eval-<round>`.

---

## G. ACP / MCP / external agent protocols

Source: `acp_adapter/server.py`, `mcp_serve.py`, `tools/mcp_tool.py`, docs `/docs/reference/cli-commands` § hermes acp / hermes mcp.

- **ACP (Agent Client Protocol):** `hermes acp` runs Hermes as an ACP **stdio** server. Designed for editor integration (Zed, Cursor, VS Code, JetBrains). The schema imports include `NewSessionResponse`, `PromptResponse`, `ResumeSessionResponse`, `ListSessionsResponse`, `LoadSessionResponse` etc. — this **is** the protocol that exposes "send a prompt to Hermes, get response back" programmatically, with first-class session lifecycle, fork, resume, model switching, MCP server attachment. Setup: `pip install -e '.[acp]'`. Entry: `hermes acp` / `hermes-acp` / `python -m acp_adapter`.
- **MCP server mode (`hermes mcp serve`):** stdio only, exposes 10 messaging-gateway tools (Section A.4). Not a prompt-execution surface.
- **MCP client mode (`hermes mcp add/list/test/configure/login`):** Hermes connects to *external* MCP servers over stdio or HTTP. So Hermes-as-client supports HTTP MCP; Hermes-as-server does not.
- **Remote driving from another box:**
  - Over SSH + stdio: `ssh miner-box hermes -z "<prompt>"` — simplest, no protocol needed, plain text back.
  - Over SSH + ACP stdio tunnel: `ssh miner-box hermes acp` with Cathedral acting as an ACP client. More structured (session lifecycle, model switching, streaming chunks via `AgentMessageChunk` and `UsageUpdate`). Recommended path for Cathedral if it needs streamed traces.
  - Over native network: not supported — no HTTP/SSE chat endpoint exists in v0.13.0.

**ACP is the closest thing Hermes has to a "drive the agent over the wire" protocol.** It's stdio, but stdio over SSH is fine.

---

## H. Versioning, install, environment

Source: `pyproject.toml:7`, `scripts/install.sh`, `/docs/getting-started/installation`.

- **Latest stable: 0.13.0.** Release notes in `/tmp/hermes-agent/RELEASE_v0.13.0.md` (63 KB — major feature additions; not crawled in detail here).
- **Install layout (per-user):** code at `~/.hermes/hermes-agent/`, binary symlink `~/.local/bin/hermes`, data at `~/.hermes/`. **Root mode:** code at `/usr/local/lib/hermes-agent/`, binary `/usr/local/bin/hermes`, data at `/root/.hermes/`.
- **Requirements:** Python 3.11 (mandatory). Installer auto-provisions `uv`, Node v22, ripgrep, ffmpeg. Only manual prereq: git. Disk/RAM not documented; expect 2-4 GB for code + skills + sessions over time.
- **Update:** `hermes update [--check] [--backup] [--restart-gateway]`. Exit codes: 0 success, 1 error, 2 working-tree changes block pull.
- **Uninstall:** `hermes uninstall [--full] [--yes]`.
- **Platforms:** Linux, macOS, WSL2 (mature). Windows native (early beta). Termux (Android). NixOS via flake.

---

## I. Self-improving loop

Source: `run_agent.py:1981, 4116-4191, 5944, 10756, 11195, 11854, 12288`, `tools/skill_manager_tool.py`.

What actually self-improves:
1. **Skills** — agent writes new SKILL.md files via `skill_manage` after solving complex tasks. (`_iters_since_skill` counter resets when `skill_manage` is invoked — run_agent.py:12288.)
2. **MEMORY.md / USER.md** — agent appends/replaces facts via `memory_tool` every ~10 user turns (config: `memory.nudge_interval`).
3. **External memory providers** (Honcho et al.) — accumulate dialectic user models, knowledge graphs, semantic indices over time, depending on provider.

What does NOT self-modify:
- **SOUL.md** — docs explicitly state "Existing user `SOUL.md` files are never overwritten." (`/docs/user-guide/features/personality`)
- **AGENTS.md** — project-level, user-owned. No automatic write-back surface in the docs or source.
- **config.yaml** — modified only by user commands (`hermes config`, `hermes model`, etc.).
- **No model fine-tuning happens locally.** "Training" terminology in marketing refers to using Hermes session data as a *source* for trajectory generation (`batch_runner.py`, `trajectory_compressor.py`, environments/), not in-flight RL.

**Cathedral risk:** Yes, an eval visit to a long-lived `hermes chat` session WILL pollute MEMORY.md and may trigger a skill creation. Section L below describes the right isolation pattern.

---

## J. Failure modes + observability

Source: `/docs/reference/cli-commands`, `hermes_cli/doctor.py`, `hermes_cli/status.py`, `hermes_cli/debug.py`.

- **Errors:** Go to `~/.hermes/logs/agent.log`, filtered subset to `errors.log`. `hermes logs errors --since 30m -f` to tail.
- **`hermes doctor [--fix]`** — diagnoses config/dependency issues (env path, python version, missing tools, broken auth). Can attempt automatic repairs.
- **`hermes status [--all] [--deep]`** — agent/auth/platform status. `--all` returns redacted shareable summary.
- **`hermes debug share [--lines N] [--expire D] [--local]`** — bundles logs + system info and uploads to paste.rs → dpaste.com (fallback). `--local` keeps it local. Privacy-sensitive — Cathedral miners should never run this.
- **LLM provider down:** `hermes fallback` chain kicks in. `agent.api_max_retries` (default 3) controls retries before falling through. After all entries exhausted, the agent returns an error message in the final response (text, no special exit code from `-z`).
- **Cancellation:** `agent.interrupt(message)` exists (run_agent.py:5262-5298) — another thread can interrupt a running agent. From `-z` this isn't exposed.

---

## K. Hermes ecosystem

Source: `/docs/user-guide/features/skills`, README, `optional-skills/` tree.

- **Skill registries:**
  - **Official** — maintained in repo at `optional-skills/{research,devops,security,software-development,health,mlops}/`. Highest trust.
  - **skills.sh** — Vercel-hosted public directory, searchable.
  - **Well-known sites** — any HTTPS site publishing `/.well-known/skills/index.json`.
  - **GitHub taps** — `hermes skills tap add <owner/repo>` subscribes to a curated repo. Default trust = community.
  - **Direct URLs** — `hermes skills install https://example.com/SKILL.md`.
- **agentskills.io** open standard compatibility — Hermes can consume Anthropic-format skills.
- **SOUL.md / AGENTS.md examples** — `/tmp/hermes-agent/AGENTS.md` (45 KB) is the canonical agent-facing project doc and shows the format extensively. `hermes_cli/default_soul.py` provides the bundled starter SOUL.md.
- **Community published agents:** Discord at `discord.gg/NousResearch` is the discovery channel. The docs do not list specific reference agents Cathedral could benchmark against.

---

## L. Specific design questions for Cathedral

### L.1 How does Cathedral query an agent without polluting it?

The right answer is **(ii) ephemeral profile, with one nuance**.

**Recommended pattern:**
1. On the miner box, the human's primary agent runs under the **default profile** (`~/.hermes/`).
2. Cathedral, over SSH, runs every eval against a **dedicated eval profile** seeded once per epoch:
   ```bash
   HERMES_HOME=~/.hermes/profiles/cathedral-eval hermes -z "<task>" \
     --source cathedral-eval-<round-id> \
     --model <pinned> --provider <pinned>
   ```
3. The eval profile is cloned (`hermes profile create cathedral-eval --clone`) from the user's primary profile so the agent has the same SOUL.md, MEMORY.md, USER.md, and skills, but writes everything (sessions, request dumps, memory mutations, new skills) into the **eval profile's** `state.db` and disk tree. The user's primary profile is never touched.
4. **Important nuance:** if the miner's value proposition is "this agent learns from its work, so its evolved skills matter," then evals must run against the **primary profile** to capture that evolution. The eval profile would isolate Cathedral's traffic but would also lose access to the in-flight learning state. Two options:
   - **Snapshot-then-eval pattern:** Run `hermes backup` on primary (consistent SQLite snapshot via `sqlite3.backup()`), restore that snapshot into a fresh `cathedral-eval-<round>` profile, run the eval there, discard. Primary keeps progressing untouched. This is what Cathedral should do.
   - Alternative: run on primary and accept pollution, but mark eval messages with `--source` so they can be filtered out of any downstream analysis. Risky — memory and skill mutations from the eval prompt can still leak.

**Pattern (iii) — "something else"** that's worth knowing about: ACP `ForkSessionResponse` (acp_adapter/server.py imports it) suggests session forking exists in the ACP protocol. A fork-and-evaluate over ACP stdio could give per-call isolation without spinning a profile. **Unverified for production use — needs source code read at `acp_adapter/session.py`.**

### L.2 Proving the agent ran the FULL loop (skills, memory, tool calls), not a single LLM call

After a `-z` invocation, the following artifacts together prove the agent ran a real loop:

1. **`messages` rows** with `tool_calls IS NOT NULL` and corresponding `role='tool'` follow-ups in `state.db`. Count of these = number of tool calls executed.
2. **`tool_call_count` column** on the `sessions` row — denormalized counter.
3. **`request_dump_<session_id>_*.json` count** — one file per LLM API call. A "single LLM call" would produce exactly one dump; a real loop produces N.
4. **`api_call_count`** on the sessions row — denormalized counter.
5. **Per-turn `reasoning` / `reasoning_content` / `reasoning_details`** columns — present if the model emitted reasoning.
6. **`system_prompt`** on the sessions row contains the assembled prompt — Cathedral can verify it includes the expected SOUL.md, AGENTS.md, MEMORY.md snippets and the tool schemas for the toolsets Cathedral required.

For Cathedral's scoring rubric: a "shallow" cheater that bypassed the loop and just hit the LLM API directly cannot fake these artifacts because (a) they live in the miner's filesystem, (b) the file timestamps and SQLite WAL frames must line up, and (c) the `request_dump`s contain the full API request bodies which can be cross-checked for tool_choice and tools schemas. A weekly Merkle anchor over these per-eval bundles makes after-the-fact tampering detectable.

### L.3 Deterministic replay

**Hermes is not deterministic by design and the docs do not promise replay.** Sources of non-determinism:
- LLM provider sampling (temperature, top_p) — model-side, not Hermes-controlled at the seed level.
- Tool calls that hit the live network (`firecrawl`, `searxng`, terminal commands, browser).
- Wall-clock injected into prompts via `hermes_time.py`.
- Compression behavior triggered at context thresholds.

A third party CANNOT re-run a captured session and get bit-identical output. They CAN:
- Replay the **request_dump** payloads against the same provider and verify the responses match (provider-side determinism caveats apply).
- Verify the **session log** matches a Merkle root.
- Re-execute the **same prompts** against a fresh ephemeral profile with the same model/provider/seed and check that the *answer quality* lands in the same rubric band, even if text differs.

For Cathedral, this means scoring should be **rubric-based** (does the answer satisfy the criteria) rather than **reference-based** (does the answer match a golden output). The signed artifacts prove *what the agent did*, not *that the LLM would always say the same thing*.

---

## M. Risks + sharp edges

- **No HTTP chat endpoint.** Anyone building a "Hermes-as-a-service" assumption from outside has to wrap CLI or ACP. Confirmed: Cathedral's `cathedral-runtime` `/chat` wrapper was solving a non-problem.
- **`hermes mcp serve` is a gateway-control MCP, NOT a chat MCP.** Easy to misread the command name.
- **`HERMES_YOLO_MODE=1` is set automatically by `-z`.** The agent will execute dangerous commands without prompting. The miner box MUST be sandboxed (Docker terminal backend or a dedicated VM). For Cathedral evals running arbitrary task prompts ("regulatory briefs, drug discovery, video generation"), this is a real risk — a hostile task could exfiltrate the miner's `.env`.
- **Gateway uses an exclusive bot token per platform** — two profiles can't share one Telegram bot. Cathedral's eval profile cannot have its own gateway running on the same platforms as primary.
- **No air-gapped mode is documented**, but the FAQ explicitly states "API calls go only to the LLM provider you configure. Hermes Agent does not collect telemetry, usage data, or analytics." Two phone-home paths exist:
  - `hermes debug share` uploads to paste.rs/dpaste.com — opt-in only.
  - `hermes skills install <hub>` fetches over network — opt-in.
  - LLM provider calls — unavoidable unless using local Ollama.
- **Rate limits / quotas:** none documented for Hermes itself. All rate limiting is at the LLM provider layer. Credential pools (`hermes auth add`) let the agent rotate keys, which is also a vector for **abuse if Cathedral isn't careful** — a malicious miner could route Cathedral's eval traffic through their own provider pool and inflate counts.
- **WAL contention on `state.db`.** Multiple Hermes processes sharing one HERMES_HOME serialize writes (hermes_state.py:317-325). If Cathedral's eval profile and primary profile share HERMES_HOME (they shouldn't), they'll contend.
- **`checkpoints/` is per-machine and session-hash-keyed** — explicitly excluded from `hermes backup` and not portable. If Cathedral wants a complete forensic record including file-mutation rollback history, it has to capture `~/.hermes/checkpoints/` separately.
- **Memory cap (2200 chars for MEMORY.md, 1375 for USER.md)** is small. The agent will **prune** older entries to fit. For Cathedral, this means a snapshotted MEMORY.md at time T is not a strict superset of MEMORY.md at time T-1.

---

## Open questions the docs don't answer — Cathedral will need to read source

1. **Exact format and stability of the `tools` array in `session_<id>.json`.** The JSON dump (`run_agent.py:5246`) writes `self.tools` verbatim. Is it stable OpenAI tool schema across Hermes versions? If Cathedral signs that array as part of the eval receipt, version upgrades of Hermes could change shape. → Read `run_agent.py` around tool list assembly, and the per-provider adapters under `agent/`.
2. **ACP session fork semantics.** `ForkSessionResponse` is imported in `acp_adapter/server.py:27`. Can Cathedral fork-and-evaluate a live primary session via ACP to get per-call isolation without a full profile clone? Cleaner than the snapshot-then-eval pattern if it works. → Read `acp_adapter/session.py` and the `agent-client-protocol` package fork semantics.
3. **Gateway HTTP port and listening behavior.** The docs don't pin a port; `hermes_cli/web_server.py` clearly runs FastAPI on default 9119 for the dashboard, and the webhook subscription system implies the gateway listens for inbound HTTP somewhere. If Cathedral signs a snapshot that includes "this miner had a publicly exposed webhook receiver", that's relevant. → Read `gateway/` directory entry points and inspect `gateway_state.json` schema.
4. **What `--max-turns` interacts with for `-z`.** The flag is documented for `hermes chat` but `-z` doesn't expose it in `_parser.py` — the top-level parser only inherits `--toolsets`, `--model`, `--provider`. Does the agent honor `max_turns` from config.yaml during `-z`? Cathedral may want to cap turn budget per eval. → Read `run_agent.py` `AIAgent.chat()` for the iteration cap.
5. **Whether `--source` tagging actually flows through to the `sessions.source` SQLite column for `-z`.** Doc says default `source=cli`; `_parser.py` shows `--source` is on the `chat` subparser, not the top-level `-z`. If `-z` always stamps `source=cli`, Cathedral cannot filter its eval traffic by source and needs another mechanism (HERMES_HOME-per-eval or post-hoc session_id tracking). → Read `oneshot.py:_run_agent` to confirm what source is set on the AIAgent.

---

## Cathedral design implications (one-paragraph summary)

Throw out the `cathedral-runtime` Docker wrapper. The miner-side surface for Cathedral is: (1) SSH in, (2) snapshot the miner's primary `~/.hermes/` into a fresh per-eval profile via `hermes backup` + restore into `~/.hermes/profiles/cathedral-eval-<round>/`, (3) `HERMES_HOME=... hermes -z "<task>" --model <pinned> --provider <pinned>` and capture stdout, (4) hash the new SQLite session row + messages, the `session_<id>.json`, every `request_dump_<id>_*.json`, the post-eval `MEMORY.md` diff, and any new `SKILL.md` files, (5) sign that bundle, (6) anchor weekly via the `hermes backup` zip Merkle root. ACP-over-SSH-stdio is a cleaner V2 path if Cathedral wants streaming traces and native session-fork semantics. The data moat is real: a year of weekly snapshots across hundreds of miners gives Cathedral the only longitudinal dataset of self-improving agent trajectories that exists in the open.
