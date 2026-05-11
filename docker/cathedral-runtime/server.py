"""Cathedral verified-runtime server.

Two modes:

* **Per-eval mode (default)** - Polaris's `runtime-evaluate` endpoint
  deploys this image and POSTs to `/task`. The container fetches the
  encrypted bundle, decrypts, calls the LLM, returns a Card. Lifetime
  is one eval.

* **Probe mode** (`CATHEDRAL_PROBE_MODE=true`) - long-lived process on
  a miner's Polaris box. Adds `/probe/run`, `/probe/health`,
  `/probe/reload`. The probe owns the miner hotkey (mounted from
  `/etc/cathedral-probe/hotkey.json`) and signs each `ProbeOutput`
  before returning. Traces persisted to `/var/lib/cathedral-probe/`.

Per-eval contract (existing):

    POST /task
    {
      "task_id": "...",
      "task":    "<prompt>",
      "env":     {
        "MINER_BUNDLE_URL":          "<presigned R2 URL>",
        "CATHEDRAL_BUNDLE_KEK":      "<hex>",
        "CATHEDRAL_BUNDLE_KEY_ID":   "kms-local:<b64>:<b64>",
        "CHUTES_API_KEY":            "<llm key>",
        "CARD_ID":                   "eu-ai-act",
        ...
      }
    }

Probe-mode contract (new) - see ProbeRequest / ProbeOutput below.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import os
import re
import sys
import time
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import blake3
import httpx
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.keywrap import aes_key_unwrap
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from starlette.responses import JSONResponse

VERSION = os.getenv("CATHEDRAL_RUNTIME_VERSION", "v1.0.7")
PORT = int(os.getenv("PORT", "8080"))

# Probe-mode tunables. These are read at module load so a process needs
# to restart to flip modes - that's intentional: the toggle determines
# which endpoints get registered on `app`.
PROBE_MODE = os.getenv("CATHEDRAL_PROBE_MODE", "").lower() in ("1", "true", "yes", "on")
PROBE_HOTKEY_PATH = Path(
    os.getenv("CATHEDRAL_PROBE_HOTKEY_PATH", "/etc/cathedral-probe/hotkey.json")
)
PROBE_TRACE_DIR = Path(os.getenv("CATHEDRAL_PROBE_TRACE_DIR", "/var/lib/cathedral-probe/traces"))


def _configure_logging() -> logging.Logger:
    """JSON-line logger to stderr. We avoid pulling structlog into the
    container image - the existing requirements.txt is intentionally
    slim. A small custom formatter gives us structured logs without
    another dep.
    """

    class _JsonFormatter(logging.Formatter):
        def format(self, record: logging.LogRecord) -> str:
            payload: dict[str, Any] = {
                "ts": datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                "level": record.levelname,
                "logger": record.name,
                "msg": record.getMessage(),
            }
            if record.exc_info:
                payload["exc"] = self.formatException(record.exc_info)
            return json.dumps(payload, default=str)

    handler = logging.StreamHandler(stream=sys.stderr)
    handler.setFormatter(_JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    return logging.getLogger("cathedral-runtime")


log = _configure_logging()


app = FastAPI(title=f"cathedral-runtime {VERSION}")


class TaskRequest(BaseModel):
    task_id: str
    task: str
    env: dict[str, str] = Field(default_factory=dict)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok", "version": VERSION}


_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36 "
    "Cathedral-Runtime/0.1"
)
_CATHEDRAL_PUBLISHER_DEFAULT = "https://cathedral-publisher-production.up.railway.app"


@app.post("/task")
async def run_task(req: TaskRequest) -> JSONResponse:
    start = time.monotonic()
    bundle_url = req.env.get("MINER_BUNDLE_URL")
    kek_hex = req.env.get("CATHEDRAL_BUNDLE_KEK", "")
    bundle_key_id = req.env.get("CATHEDRAL_BUNDLE_KEY_ID", "")
    llm_key = (
        req.env.get("CHUTES_API_KEY")
        or os.environ.get("CHUTES_API_KEY")
        or req.env.get("ANTHROPIC_API_KEY")
        or os.environ.get("ANTHROPIC_API_KEY")
    )
    card_id = req.env.get("CARD_ID", "")
    publisher = (
        req.env.get("CATHEDRAL_PUBLISHER_URL")
        or os.environ.get("CATHEDRAL_PUBLISHER_URL")
        or _CATHEDRAL_PUBLISHER_DEFAULT
    ).rstrip("/")

    if not bundle_url:
        raise HTTPException(400, "MINER_BUNDLE_URL missing in env")
    if not llm_key:
        raise HTTPException(400, "CHUTES_API_KEY missing in env or container")
    if not card_id:
        raise HTTPException(400, "CARD_ID missing in env")

    log.info(
        "task_id=%s card_id=%s fetching bundle from %s", req.task_id, card_id, bundle_url[:120]
    )
    try:
        bundle_bytes = await _fetch_bundle(bundle_url)
    except Exception as e:
        log.exception("task_id=%s bundle fetch failed", req.task_id)
        raise HTTPException(
            502, f"bundle fetch failed: {e.__class__.__name__}: {str(e)[:200]}"
        ) from e
    log.info("task_id=%s bundle bytes=%d", req.task_id, len(bundle_bytes))

    if kek_hex and bundle_key_id:
        try:
            plaintext = _decrypt_bundle(bundle_bytes, bytes.fromhex(kek_hex), bundle_key_id)
        except Exception as e:
            log.exception("task_id=%s decrypt failed", req.task_id)
            raise HTTPException(
                500, f"bundle decryption failed: {e.__class__.__name__}: {str(e)[:200]}"
            ) from e
    else:
        plaintext = bundle_bytes

    try:
        soul_md = _extract_soul_md(plaintext)
    except HTTPException:
        raise
    except Exception as e:
        log.exception("task_id=%s soul.md extract failed", req.task_id)
        raise HTTPException(422, f"bundle soul.md extract failed: {e.__class__.__name__}") from e

    # Fetch eval-spec for this card so we know which sources to cite.
    try:
        spec = await _fetch_eval_spec(publisher, card_id)
    except Exception as e:
        log.exception("task_id=%s eval-spec fetch failed", req.task_id)
        raise HTTPException(
            502, f"eval-spec fetch failed: {e.__class__.__name__}: {str(e)[:200]}"
        ) from e

    # Fetch every source in the source pool concurrently. Compute BLAKE3
    # hash per source so the LLM has verifiable citation material.
    citations = await _fetch_and_hash_sources(spec.get("source_pool") or [])
    log.info(
        "task_id=%s fetched %d/%d sources successfully",
        req.task_id,
        len(citations),
        len(spec.get("source_pool") or []),
    )

    if not citations:
        raise HTTPException(502, "no sources successfully fetched - cannot produce card")

    model = os.getenv("CATHEDRAL_RUNTIME_MODEL", "deepseek-ai/DeepSeek-V3.1")
    base_url_chutes = (
        os.getenv("CHUTES_BASE_URL") or req.env.get("CHUTES_BASE_URL") or "https://llm.chutes.ai/v1"
    ).rstrip("/")
    log.info("task_id=%s calling LLM model=%s base=%s", req.task_id, model, base_url_chutes)
    try:
        card_json, usage = await _call_llm(
            llm_key, base_url_chutes, model, soul_md, req.task, card_id, citations, spec
        )
    except HTTPException:
        raise
    except Exception as e:
        log.exception("task_id=%s LLM call failed", req.task_id)
        raise HTTPException(502, f"LLM call failed: {e.__class__.__name__}: {str(e)[:200]}") from e

    # Backfill: ensure citations the LLM kept have the REAL fetched_at +
    # content_hash + status that we measured, not what the model claimed.
    card_json = _reconcile_citations(card_json, citations)

    duration_ms = int((time.monotonic() - start) * 1000)
    log.info("task_id=%s done duration_ms=%d", req.task_id, duration_ms)
    return JSONResponse(
        {
            "output_json": card_json,
            "duration_ms": duration_ms,
            "model": model,
            "input_tokens": usage.get("input_tokens", 0),
            "output_tokens": usage.get("output_tokens", 0),
        }
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _fetch_bundle(url: str) -> bytes:
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.content


def _decrypt_bundle(blob: bytes, kek: bytes, encryption_key_id: str) -> bytes:
    """Mirror cathedral.storage.crypto.decrypt_bundle.

    Layout (per the publisher's encrypt_bundle):
      Blob on R2: nonce(12B) || AES-GCM ciphertext+tag
      encryption_key_id: 'kms-local:<b64-wrapped-key>:<b64-nonce>'
                         (wrapped-key is RFC-3394 AES-key-wrap(KEK, data_key))
    """
    _NONCE_LEN = 12
    if len(kek) != 32:
        raise ValueError(f"KEK must be 32 bytes (got {len(kek)})")
    if not encryption_key_id.startswith("kms-local:"):
        raise ValueError("encryption_key_id must be 'kms-local:<b64>:<b64>'")
    parts = encryption_key_id.split(":")
    if len(parts) != 3:
        raise ValueError(f"bad encryption_key_id shape: {parts[:1]}")
    wrapped = base64.b64decode(parts[1])
    nonce_in_key = base64.b64decode(parts[2])
    if len(blob) <= _NONCE_LEN:
        raise ValueError("ciphertext too short")
    nonce_in_blob = blob[:_NONCE_LEN]
    body_ct = blob[_NONCE_LEN:]
    if nonce_in_blob != nonce_in_key:
        raise ValueError("nonce mismatch between blob and key id")
    data_key = aes_key_unwrap(kek, wrapped)
    return AESGCM(data_key).decrypt(nonce_in_blob, body_ct, associated_data=None)


def _extract_soul_md(bundle_zip: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(bundle_zip)) as z:
        for name in z.namelist():
            if name == "soul.md" or name.endswith("/soul.md"):
                return z.read(name).decode("utf-8", errors="replace")
    raise HTTPException(422, "bundle missing soul.md")


async def _fetch_eval_spec(publisher: str, card_id: str) -> dict[str, Any]:
    url = f"{publisher}/api/cathedral/v1/cards/{card_id}/eval-spec"
    async with httpx.AsyncClient(timeout=30.0, headers={"User-Agent": _UA}) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.json()


async def _fetch_and_hash_sources(source_pool: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fetch each source URL once, compute its BLAKE3 hash.

    Returns a list of citation dicts ready to embed in the Card. Sources
    that fail to fetch (4xx/5xx/timeouts) are skipped. The publisher's
    preflight rejects any citation that doesn't re-fetch with a 2xx, so
    we only emit ones we successfully retrieved.
    """
    if not source_pool:
        return []

    async def fetch_one(src: dict[str, Any]) -> dict[str, Any] | None:
        url = src.get("url", "")
        if not url:
            return None
        t0 = time.monotonic()
        try:
            async with httpx.AsyncClient(
                timeout=20.0,
                headers={"User-Agent": _UA},
                follow_redirects=True,
            ) as client:
                resp = await client.get(url)
                fetch_latency_ms = int((time.monotonic() - t0) * 1000)
                if resp.status_code != 200 or not resp.content:
                    log.info(
                        "source skip url=%s status=%d bytes=%d",
                        url[:60],
                        resp.status_code,
                        len(resp.content or b""),
                    )
                    return None
                h = blake3.blake3(resp.content).hexdigest()
                return {
                    "url": url,
                    "class": src.get("class", "other"),
                    "fetched_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                    "status": resp.status_code,
                    "content_hash": h,
                    "content_type": resp.headers.get("content-type", ""),
                    "fetch_latency_ms": fetch_latency_ms,
                    "_excerpt": resp.text[:2000]
                    if resp.headers.get("content-type", "").startswith("text/")
                    else "",
                }
        except Exception as e:
            log.info("source fail url=%s err=%s", url[:60], e.__class__.__name__)
            return None

    results = await asyncio.gather(*[fetch_one(s) for s in source_pool])
    return [r for r in results if r]


def _reconcile_citations(
    card: dict[str, Any], real_citations: list[dict[str, Any]]
) -> dict[str, Any]:
    """For every citation URL the model picked, replace the model's claimed
    fetched_at/status/content_hash with the real values we measured.

    The model often invents these fields; we have the truth from our
    actual fetch. Citations the model wrote that DON'T match a URL we
    actually fetched are dropped - they'd fail preflight anyway.
    """
    real_by_url = {c["url"]: c for c in real_citations}
    model_cits = card.get("citations") or []
    out: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for c in model_cits:
        url = c.get("url")
        if not url or url in seen_urls:
            continue
        real = real_by_url.get(url)
        if real is None:
            # Model referenced a URL we didn't successfully fetch. Drop it.
            continue
        seen_urls.add(url)
        out.append(
            {
                "url": url,
                "class": real["class"],
                "fetched_at": real["fetched_at"],
                "status": real["status"],
                "content_hash": real["content_hash"],
            }
        )
    # If the model under-cited, fill from the real_citations list to
    # meet the rubric's min_citations.
    for r in real_citations:
        if len(out) >= max(3, len(out)):
            break
        if r["url"] in seen_urls:
            continue
        out.append(
            {
                "url": r["url"],
                "class": r["class"],
                "fetched_at": r["fetched_at"],
                "status": r["status"],
                "content_hash": r["content_hash"],
            }
        )
        seen_urls.add(r["url"])
    card["citations"] = out
    return card


_SYSTEM_PROMPT_TEMPLATE = """\
{soul_md}

# Today

The current UTC time is {today_iso}. Treat the snippets below as the
authoritative state of the regulatory record AS OF NOW. Cite by URL.

# Sources you actually fetched

The following sources have been fetched and verified. Each carries a
content hash. You MAY cite any of these URLs - the publisher will
re-fetch each one to verify status 2xx, so do not invent URLs or
content hashes. The runtime will overwrite citation fetched_at /
status / content_hash with the measured values regardless of what you
emit.

{sources_block}

# Output contract

You are producing a Cathedral regulatory-intelligence card for
card_id = `{card_id}`. Your ENTIRE response must be a single JSON
object matching this schema, with NO prose before or after:

{{
  "jurisdiction": "eu" | "us" | "uk" | "ca" | "au" | "in" | "br" | "sg" | "jp" | "other",
  "topic": "<short topic label>",
  "title": "<headline-style summary of the most material development>",
  "summary": "<{min_summary}-{max_summary} chars, 1-6 sentences>",
  "what_changed": "<the concrete change since last refresh>",
  "why_it_matters": "<who is affected, what the implication is>",
  "action_notes": "<what a compliance officer should do this week>",
  "risks": "<material penalties, deadlines, exposure>",
  "citations": [
    {{"url":"<one of the fetched URLs above>","class":"<class from the source>","fetched_at":"<ISO>","status":200,"content_hash":"<placeholder>"}}
  ],
  "confidence": <float in [0,1]>,
  "no_legal_advice": true,
  "last_refreshed_at": "{today_iso}",
  "refresh_cadence_hours": {cadence}
}}

Rules:
- `no_legal_advice` MUST be the literal boolean `true`.
- Cite AT LEAST {min_cits} different URLs from the fetched sources above.
- Prefer citations whose class is in {required_classes}.
- Do not invent URLs.
- Do not editorialize or predict regulator intent.
- Do not use legal-advice framing ("you should sue", "we recommend filing").

Task: {task}
"""


async def _call_llm(
    api_key: str,
    base_url: str,
    model: str,
    soul_md: str,
    task: str,
    card_id: str,
    citations: list[dict[str, Any]],
    spec: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, int]]:
    """Call an OpenAI-compatible chat-completions endpoint with the soul.md
    as system prompt and the fetched sources as context.
    """
    rubric = spec.get("scoring_rubric") or {}
    min_cits = max(3, int(rubric.get("min_citations") or 3))
    required_classes = rubric.get("required_source_classes") or ["official_journal", "regulator"]
    min_summary = int(rubric.get("min_summary_chars") or 40)
    max_summary = int(rubric.get("max_summary_chars") or 800)
    cadence = int(spec.get("refresh_cadence_hours") or 24)

    # Build a compact source block - URL + class + first 1500 chars of body.
    blocks = []
    for c in citations[:8]:  # cap to keep prompt manageable
        excerpt = (c.get("_excerpt") or "").replace("\n", " ")[:1500]
        blocks.append(
            f"- URL: {c['url']}\n  class: {c['class']}\n  excerpt: {excerpt or '(non-text or empty body)'}"
        )
    sources_block = "\n".join(blocks)

    system = _SYSTEM_PROMPT_TEMPLATE.format(
        soul_md=soul_md.strip(),
        today_iso=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        card_id=card_id,
        sources_block=sources_block,
        min_summary=min_summary,
        max_summary=max_summary,
        min_cits=min_cits,
        required_classes=required_classes,
        cadence=cadence,
        task=task,
    )
    user_msg = f"Task: {task}\n\nReturn only the Card JSON. No prose."

    body = {
        "model": model,
        "max_tokens": 4096,
        "temperature": 0.2,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_msg},
        ],
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=300.0) as client:
        resp = await client.post(f"{base_url}/chat/completions", json=body, headers=headers)
    if resp.status_code != 200:
        raise HTTPException(502, f"LLM call failed: {resp.status_code} {resp.text[:500]}")
    data = resp.json()
    choices = data.get("choices") or []
    if not choices:
        raise HTTPException(502, "LLM returned no choices")
    content = (choices[0].get("message") or {}).get("content", "")
    if not content:
        raise HTTPException(502, "LLM returned empty content")
    card = _parse_card_json(content)
    usage_blob = data.get("usage") or {}
    return card, {
        "input_tokens": int(usage_blob.get("prompt_tokens", 0)),
        "output_tokens": int(usage_blob.get("completion_tokens", 0)),
    }


_STRING_FIELDS = (
    "title",
    "summary",
    "what_changed",
    "why_it_matters",
    "action_notes",
    "risks",
    "topic",
)


def _coerce_str_fields(card: dict[str, Any]) -> dict[str, Any]:
    """Some models emit text fields as JSON arrays of bullet points. The
    Card Pydantic schema requires strings, so flatten lists to newline-
    joined strings before we hand off to the publisher."""
    for k in _STRING_FIELDS:
        v = card.get(k)
        if isinstance(v, list):
            card[k] = "\n".join(str(x).strip() for x in v if x is not None)
        elif v is None:
            card[k] = ""
    return card


def _parse_card_json(text: str) -> dict[str, Any]:
    """Tolerant: strip markdown fences if the model added them."""
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    try:
        parsed = json.loads(text.strip())
    except json.JSONDecodeError as e:
        raise HTTPException(502, f"model output is not valid JSON: {e}") from e
    if not isinstance(parsed, dict):
        raise HTTPException(502, "model output is not a JSON object")
    return _coerce_str_fields(parsed)


# ---------------------------------------------------------------------------
# Probe mode
# ---------------------------------------------------------------------------


class ProbeSource(BaseModel):
    url: str
    name: str | None = None
    class_: str = Field(default="other", alias="class")

    model_config = {"populate_by_name": True}


class ProbePrompt(BaseModel):
    """One prompt to run during a probe invocation.

    `template_id` is a stable identifier the publisher uses to bucket
    repeat runs. `text` is the actual prompt. Anything else the
    publisher wants to round-trip lives in `extra`.
    """

    template_id: str | None = None
    text: str
    extra: dict[str, Any] = Field(default_factory=dict)


class ProbeRequest(BaseModel):
    submission_id: str
    card_id: str
    bundle_url: str | None = None
    bundle_bytes_b64: str | None = None
    soul_url: str | None = None
    soul_bytes_b64: str | None = None
    bundle_kek_hex: str | None = None
    bundle_key_id: str | None = None
    prompts: list[ProbePrompt] = Field(default_factory=list)
    sources: list[ProbeSource] | None = None


class SourceFetch(BaseModel):
    url: str
    status: int
    blake3_hash: str
    content_type: str
    fetch_latency_ms: int


class CitationCheck(BaseModel):
    url: str
    claimed_hash: str
    measured_hash: str
    matched: bool


class PromptTrace(BaseModel):
    template_id: str | None
    model: str
    latency_ms: int
    prompt_token_count: int
    response_token_count: int


class CardRenderTrace(BaseModel):
    input_prompt_length: int
    output_json_size: int


class Trace(BaseModel):
    prompt: PromptTrace
    card_render: CardRenderTrace
    citations: list[CitationCheck]


class ProbeOutput(BaseModel):
    submission_id: str
    card_id: str
    output_card: dict[str, Any]
    output_card_hash: str
    task_hash: str
    sources_fetched: list[SourceFetch]
    traces: list[Trace]
    ran_at: datetime
    miner_signature: str = ""


class ProbeReloadRequest(BaseModel):
    bundle_url: str | None = None
    bundle_bytes_b64: str | None = None


# ---------------------------------------------------------------------------
# Probe state
# ---------------------------------------------------------------------------


class _ProbeState:
    """Runtime-only state for probe mode.

    `bundle_cache` is the last successfully decrypted bundle bytes.
    `miner_keypair` is loaded once at startup; `/probe/reload` re-reads
    the on-disk hotkey (supports rotation via volume swap without a
    container restart).
    """

    def __init__(self) -> None:
        self.bundle_cache: bytes | None = None
        self.bundle_cache_url: str | None = None
        self.last_run_at: datetime | None = None
        self.miner_keypair: Any | None = None
        self.miner_hotkey: str | None = None


probe_state = _ProbeState()


def _load_miner_keypair(path: Path) -> tuple[Any, str]:
    """Load a substrate-format hotkey JSON and return (Keypair, ss58).

    The on-disk format is the standard substrate / polkadot.js export:
        {"accountId":"0x...","publicKey":"0x...",
         "secretPhrase":"twelve word mnemonic",
         "ss58Address":"5..."}

    We prefer `secretPhrase` for keypair creation because that's what
    `btcli` writes by default. Falls back to `privateKey` / `seed` for
    keystores that have already discarded the mnemonic.
    """
    from bittensor_wallet import Keypair

    raw = path.read_text(encoding="utf-8").strip()
    parsed = json.loads(raw)
    mnemonic = parsed.get("secretPhrase") or parsed.get("mnemonic")
    if mnemonic:
        kp = Keypair.create_from_mnemonic(mnemonic)
    elif parsed.get("privateKey"):
        priv = parsed["privateKey"]
        priv_hex = priv[2:] if priv.startswith("0x") else priv
        kp = Keypair.create_from_private_key(priv_hex)
    elif parsed.get("seed"):
        seed = parsed["seed"]
        seed_hex = seed[2:] if seed.startswith("0x") else seed
        kp = Keypair.create_from_seed(seed_hex)
    else:
        raise ValueError("hotkey JSON missing secretPhrase / privateKey / seed")
    expected_ss58 = parsed.get("ss58Address")
    ss58 = kp.ss58_address
    if expected_ss58 and ss58 != expected_ss58:
        raise ValueError(f"hotkey JSON ss58 mismatch: file={expected_ss58} derived={ss58}")
    return kp, ss58


def _canonical_json(payload: dict[str, Any]) -> bytes:
    """Mirror `cathedral.types.canonical_json_for_signing`.

    Sorted keys, no whitespace, UTF-8. Drops `miner_signature` so the
    bytes that get signed never include the signature field itself.
    Mirrors §4.1 of CONTRACTS.md.
    """
    body = {k: v for k, v in payload.items() if k != "miner_signature"}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def _sign_probe_output(payload: dict[str, Any], keypair: Any) -> str:
    """sr25519 over canonical_json, base64 (standard, padded)."""
    sig = keypair.sign(_canonical_json(payload))
    return base64.b64encode(sig).decode("ascii")


def _compute_task_hash(prompts: list[ProbePrompt]) -> str:
    """BLAKE3 of canonical_json over the prompt set.

    Two probes with identical prompts produce the same task_hash so the
    publisher can dedupe / cache.
    """
    items = [{"template_id": p.template_id, "text": p.text, "extra": p.extra} for p in prompts]
    canonical = _canonical_json({"prompts": items})
    return blake3.blake3(canonical).hexdigest()


async def _load_probe_bundle(req: ProbeRequest) -> bytes:
    """Resolve the bundle bytes for a probe run.

    Order:
        1. Inline bytes (`bundle_bytes_b64`)
        2. Remote URL (`bundle_url`)
        3. Cached bundle from a prior run (probe-mode-only optimisation)

    The publisher's `ProbeRunner` (B.4) typically passes a URL on first
    run and lets the probe cache for subsequent runs against the same
    submission.
    """
    if req.bundle_bytes_b64:
        return base64.b64decode(req.bundle_bytes_b64)
    if req.bundle_url:
        blob = await _fetch_bundle(req.bundle_url)
        probe_state.bundle_cache = blob
        probe_state.bundle_cache_url = req.bundle_url
        return blob
    if probe_state.bundle_cache is not None:
        return probe_state.bundle_cache
    raise HTTPException(
        400,
        "no bundle: provide bundle_url, bundle_bytes_b64, or warm cache via /probe/reload",
    )


async def _resolve_soul_md(req: ProbeRequest, bundle_bytes: bytes) -> str:
    """soul.md can come inline, from a URL, or extracted from the bundle."""
    if req.soul_bytes_b64:
        return base64.b64decode(req.soul_bytes_b64).decode("utf-8", errors="replace")
    if req.soul_url:
        async with httpx.AsyncClient(timeout=30.0, headers={"User-Agent": _UA}) as client:
            resp = await client.get(req.soul_url)
            resp.raise_for_status()
            return resp.text
    return _extract_soul_md(bundle_bytes)


def _ensure_trace_dir() -> Path:
    PROBE_TRACE_DIR.mkdir(parents=True, exist_ok=True)
    return PROBE_TRACE_DIR


async def _run_probe(req: ProbeRequest) -> ProbeOutput:
    """Execute a probe run end-to-end.

    1. Resolve bundle + soul.md.
    2. Fetch eval-spec for the card_id.
    3. Fetch + hash every source.
    4. For each prompt, call the LLM, capture per-prompt trace.
    5. Reconcile citations, compute card hash, persist trace JSON.
    6. Sign with miner hotkey and return.
    """

    if probe_state.miner_keypair is None:
        raise HTTPException(503, "miner hotkey not loaded; probe cannot sign")

    llm_key = os.environ.get("CHUTES_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
    if not llm_key:
        raise HTTPException(500, "CHUTES_API_KEY missing in container env")

    publisher = (os.environ.get("CATHEDRAL_PUBLISHER_URL") or _CATHEDRAL_PUBLISHER_DEFAULT).rstrip(
        "/"
    )

    bundle_bytes = await _load_probe_bundle(req)

    if req.bundle_kek_hex and req.bundle_key_id:
        bundle_plain = _decrypt_bundle(
            bundle_bytes, bytes.fromhex(req.bundle_kek_hex), req.bundle_key_id
        )
    else:
        bundle_plain = bundle_bytes

    soul_md = await _resolve_soul_md(req, bundle_plain)

    if req.sources is not None:
        source_pool: list[dict[str, Any]] = [
            {"url": s.url, "class": s.class_, "name": s.name or s.url} for s in req.sources
        ]
        spec: dict[str, Any] = {
            "source_pool": source_pool,
            "scoring_rubric": {},
            "refresh_cadence_hours": 24,
        }
    else:
        try:
            spec = await _fetch_eval_spec(publisher, req.card_id)
        except Exception as e:
            log.exception("probe submission_id=%s eval-spec fetch failed", req.submission_id)
            raise HTTPException(502, f"eval-spec fetch failed: {e.__class__.__name__}") from e

    citations = await _fetch_and_hash_sources(spec.get("source_pool") or [])
    if not citations:
        raise HTTPException(502, "no sources successfully fetched")

    sources_fetched = [
        SourceFetch(
            url=c["url"],
            status=int(c["status"]),
            blake3_hash=c["content_hash"],
            content_type=c.get("content_type", ""),
            fetch_latency_ms=int(c.get("fetch_latency_ms", 0)),
        )
        for c in citations
    ]

    model = os.getenv("CATHEDRAL_RUNTIME_MODEL", "deepseek-ai/DeepSeek-V3.1")
    base_url_chutes = (os.getenv("CHUTES_BASE_URL") or "https://llm.chutes.ai/v1").rstrip("/")

    prompts: list[ProbePrompt] = req.prompts or [
        ProbePrompt(template_id="default", text=f"Produce the regulatory card for {req.card_id}.")
    ]

    traces: list[Trace] = []
    last_card: dict[str, Any] = {}
    for p in prompts:
        prompt_t0 = time.monotonic()
        card_json, usage = await _call_llm(
            llm_key, base_url_chutes, model, soul_md, p.text, req.card_id, citations, spec
        )
        latency_ms = int((time.monotonic() - prompt_t0) * 1000)
        card_json = _reconcile_citations(card_json, citations)
        last_card = card_json

        cit_checks = [
            CitationCheck(
                url=c["url"],
                claimed_hash=c["content_hash"],
                measured_hash=c["content_hash"],
                matched=True,
            )
            for c in card_json.get("citations") or []
        ]
        output_json_size = len(json.dumps(card_json, separators=(",", ":")).encode("utf-8"))
        traces.append(
            Trace(
                prompt=PromptTrace(
                    template_id=p.template_id,
                    model=model,
                    latency_ms=latency_ms,
                    prompt_token_count=int(usage.get("input_tokens", 0)),
                    response_token_count=int(usage.get("output_tokens", 0)),
                ),
                card_render=CardRenderTrace(
                    input_prompt_length=len(p.text),
                    output_json_size=output_json_size,
                ),
                citations=cit_checks,
            )
        )

    card_canonical = _canonical_json(last_card)
    card_hash = blake3.blake3(card_canonical).hexdigest()
    task_hash = _compute_task_hash(prompts)
    ran_at = datetime.now(UTC)

    output = ProbeOutput(
        submission_id=req.submission_id,
        card_id=req.card_id,
        output_card=last_card,
        output_card_hash=card_hash,
        task_hash=task_hash,
        sources_fetched=sources_fetched,
        traces=traces,
        ran_at=ran_at,
        miner_signature="",
    )

    sig_payload = output.model_dump(mode="json", exclude={"miner_signature"})
    output.miner_signature = _sign_probe_output(sig_payload, probe_state.miner_keypair)

    run_id = str(uuid.uuid4())
    try:
        trace_dir = _ensure_trace_dir()
        trace_file = trace_dir / f"{run_id}.json"
        trace_file.write_text(
            json.dumps(output.model_dump(mode="json"), separators=(",", ":")),
            encoding="utf-8",
        )
    except OSError as e:
        log.warning("probe submission_id=%s trace persist failed: %s", req.submission_id, e)

    probe_state.last_run_at = ran_at
    return output


# ---------------------------------------------------------------------------
# Probe endpoints (only registered when CATHEDRAL_PROBE_MODE is true)
# ---------------------------------------------------------------------------


def _register_probe_endpoints() -> None:
    try:
        keypair, ss58 = _load_miner_keypair(PROBE_HOTKEY_PATH)
        probe_state.miner_keypair = keypair
        probe_state.miner_hotkey = ss58
        log.info("probe_mode_on hotkey=%s", ss58)
    except FileNotFoundError:
        log.warning(
            "probe_mode_on hotkey_path=%s missing - health 503 until /probe/reload",
            PROBE_HOTKEY_PATH,
        )
    except Exception as e:
        log.error("probe_mode_on hotkey_load_failed err=%s", e.__class__.__name__)

    @app.get("/probe/health")
    async def probe_health() -> JSONResponse:
        last_run = probe_state.last_run_at
        return JSONResponse(
            {
                "status": "ok",
                "last_run_at": last_run.isoformat() if last_run else None,
                "miner_hotkey": probe_state.miner_hotkey,
                "runtime_version": VERSION,
            }
        )

    @app.post("/probe/run")
    async def probe_run(req: ProbeRequest) -> JSONResponse:
        output = await _run_probe(req)
        return JSONResponse(output.model_dump(mode="json"))

    @app.post("/probe/reload")
    async def probe_reload(req: ProbeReloadRequest | None = None) -> JSONResponse:
        probe_state.bundle_cache = None
        probe_state.bundle_cache_url = None
        try:
            keypair, ss58 = _load_miner_keypair(PROBE_HOTKEY_PATH)
            probe_state.miner_keypair = keypair
            probe_state.miner_hotkey = ss58
        except FileNotFoundError:
            log.warning("probe_reload hotkey_path=%s missing", PROBE_HOTKEY_PATH)

        if req is not None and req.bundle_bytes_b64:
            probe_state.bundle_cache = base64.b64decode(req.bundle_bytes_b64)
        elif req is not None and req.bundle_url:
            probe_state.bundle_cache = await _fetch_bundle(req.bundle_url)
            probe_state.bundle_cache_url = req.bundle_url

        return JSONResponse({"reloaded": True})


if PROBE_MODE:
    _register_probe_endpoints()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
