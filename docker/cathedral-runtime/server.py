"""Cathedral verified-runtime server.

Polaris's `runtime-evaluate` endpoint deploys this image, then POSTs a task
to `/task`. We fetch the miner's encrypted bundle from R2 by the presigned
URL we receive in `env_overrides`, decrypt with the KEK Polaris injects,
load the miner's `soul.md` from the bundle, call the configured LLM
provider with that as the system prompt + the supplied task, parse the
response as Card JSON, and return it.

Contract with Polaris (`polaris/services/runtime_evaluate.py`):

    POST /task
    Content-Type: application/json
    {
      "task_id":  "...",
      "task":     "...",
      "env":      {"MINER_BUNDLE_URL": "...", "CATHEDRAL_BUNDLE_KEK": "<hex>",
                   "CHUTES_API_KEY": "...", "CARD_ID": "...", "MINER_HOTKEY": "..."}
    }

    -> 200
    {
      "output_json":   {... Card ...},
      "duration_ms":   12345,
      "model":         "deepseek-ai/DeepSeek-V3.1",
      "input_tokens":  ...,
      "output_tokens": ...
    }

    /healthz returns 200 once the model client is initialised.

LLM provider: Chutes (https://llm.chutes.ai/v1) by default, matching
Polaris's Hermes runtime. Any OpenAI-compatible provider works — drop a
different `CHUTES_BASE_URL` + key into the container env and the call
shape is unchanged.

The bundle decryption mirrors `cathedral.storage.crypto.decrypt_bundle`
exactly so any KEK that worked on the publisher works here.
"""

from __future__ import annotations

import io
import json
import logging
import os
import re
import time
import zipfile
from typing import Any

import httpx
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from starlette.responses import JSONResponse

VERSION = os.getenv("CATHEDRAL_RUNTIME_VERSION", "v1.0.1")
PORT = int(os.getenv("PORT", "8080"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("cathedral-runtime")


app = FastAPI(title=f"cathedral-runtime {VERSION}")


class TaskRequest(BaseModel):
    task_id: str
    task: str
    env: dict[str, str] = Field(default_factory=dict)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok", "version": VERSION}


@app.post("/task")
async def run_task(req: TaskRequest) -> JSONResponse:
    start = time.monotonic()
    bundle_url = req.env.get("MINER_BUNDLE_URL")
    kek_hex = req.env.get("CATHEDRAL_BUNDLE_KEK", "")
    llm_key = (
        req.env.get("CHUTES_API_KEY")
        or os.environ.get("CHUTES_API_KEY")
        or req.env.get("ANTHROPIC_API_KEY")  # backward compat shim
        or os.environ.get("ANTHROPIC_API_KEY")
    )
    card_id = req.env.get("CARD_ID", "")

    if not bundle_url:
        raise HTTPException(400, "MINER_BUNDLE_URL missing in env")
    if not llm_key:
        raise HTTPException(400, "CHUTES_API_KEY missing in env or container")

    log.info("task_id=%s card_id=%s fetching bundle", req.task_id, card_id)
    bundle_bytes = await _fetch_bundle(bundle_url)
    log.info("task_id=%s bundle bytes=%d", req.task_id, len(bundle_bytes))

    if kek_hex:
        try:
            plaintext = _decrypt_bundle(bundle_bytes, bytes.fromhex(kek_hex))
        except Exception as e:
            log.exception("task_id=%s decrypt failed", req.task_id)
            raise HTTPException(500, f"bundle decryption failed: {e}") from e
    else:
        plaintext = bundle_bytes

    soul_md = _extract_soul_md(plaintext)

    model = os.getenv("CATHEDRAL_RUNTIME_MODEL", "deepseek-ai/DeepSeek-V3.1")
    base_url = os.getenv("CHUTES_BASE_URL", "https://llm.chutes.ai/v1").rstrip("/")
    log.info("task_id=%s calling LLM model=%s", req.task_id, model)
    card_json, usage = await _call_llm(
        llm_key, base_url, model, soul_md, req.task, card_id
    )

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


def _decrypt_bundle(ciphertext: bytes, kek: bytes) -> bytes:
    """Match `cathedral.storage.crypto.decrypt_bundle`.

    Layout written by `encrypt_bundle`:
      4-byte big-endian wrapped-key length |
      wrapped data-key (AES-GCM wrap under KEK) |
      12-byte data-key nonce |
      ciphertext+tag of the bundle.
    """
    if len(kek) != 32:
        raise ValueError(f"KEK must be 32 bytes (got {len(kek)})")
    if len(ciphertext) < 4:
        raise ValueError("ciphertext too short")
    wrap_len = int.from_bytes(ciphertext[:4], "big")
    if wrap_len <= 0 or wrap_len > 1024:
        raise ValueError(f"implausible wrapped-key length: {wrap_len}")
    if len(ciphertext) < 4 + wrap_len + 12:
        raise ValueError("ciphertext truncated")
    wrapped_key = ciphertext[4 : 4 + wrap_len]
    body_nonce = ciphertext[4 + wrap_len : 4 + wrap_len + 12]
    body_ct = ciphertext[4 + wrap_len + 12 :]

    wrap_nonce = wrapped_key[:12]
    wrap_ct = wrapped_key[12:]
    data_key = AESGCM(kek).decrypt(wrap_nonce, wrap_ct, associated_data=None)
    return AESGCM(data_key).decrypt(body_nonce, body_ct, associated_data=None)


def _extract_soul_md(bundle_zip: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(bundle_zip)) as z:
        for name in z.namelist():
            if name == "soul.md" or name.endswith("/soul.md"):
                return z.read(name).decode("utf-8", errors="replace")
    raise HTTPException(422, "bundle missing soul.md")


_SYSTEM_PROMPT_TEMPLATE = """\
{soul_md}

# Output contract

You are producing a Cathedral regulatory-intelligence card. Your entire
response MUST be a single JSON object matching this schema, with no
prose before or after the JSON:

{{
  "jurisdiction": "eu" | "us" | "uk" | "ca" | "au" | "in" | "br" | "sg" | "jp" | "other",
  "topic": "...",
  "title": "...",
  "summary": "<40-800 chars, 1-6 sentences>",
  "what_changed": "...",
  "why_it_matters": "...",
  "action_notes": "...",
  "risks": "...",
  "citations": [{{
    "url": "...",
    "class": "official_journal" | "regulator" | "law_text" | "court" | "parliament" | "government" | "secondary_analysis" | "other",
    "fetched_at": "<ISO-8601 UTC>",
    "status": <int>,
    "content_hash": "<blake3 hex>"
  }}],
  "confidence": <0..1>,
  "no_legal_advice": true,
  "last_refreshed_at": "<ISO-8601 UTC>",
  "refresh_cadence_hours": <int>
}}

`no_legal_advice` must be the literal boolean `true`. Cite real sources
you actually used during your synthesis. Today is {today_iso}.
"""


async def _call_llm(
    api_key: str,
    base_url: str,
    model: str,
    soul_md: str,
    task: str,
    card_id: str,
) -> tuple[dict[str, Any], dict[str, int]]:
    """Call an OpenAI-compatible chat-completions endpoint.

    Polaris's Hermes runtime uses Chutes (https://llm.chutes.ai/v1) as
    its LLM provider; Cathedral's runtime piggy-backs on the same key
    and base URL by default so a single Polaris-side env serves both
    runtimes. Any OpenAI-compatible provider works.
    """
    from datetime import UTC, datetime

    system = _SYSTEM_PROMPT_TEMPLATE.format(
        soul_md=soul_md.strip(),
        today_iso=datetime.now(UTC).isoformat(timespec="seconds"),
    )
    user_msg = (
        f"Card: {card_id}\n\nTask: {task}\n\n"
        "Return only the Card JSON. No prose."
    )
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
        resp = await client.post(
            f"{base_url}/chat/completions", json=body, headers=headers
        )
    if resp.status_code != 200:
        raise HTTPException(
            502, f"LLM call failed: {resp.status_code} {resp.text[:500]}"
        )
    data = resp.json()
    choices = data.get("choices") or []
    if not choices:
        raise HTTPException(502, "LLM returned no choices")
    content = (choices[0].get("message") or {}).get("content", "")
    if not content:
        raise HTTPException(502, "LLM returned empty content")
    card = _parse_card_json(content)
    usage_blob = data.get("usage") or {}
    usage = {
        "input_tokens": int(usage_blob.get("prompt_tokens", 0)),
        "output_tokens": int(usage_blob.get("completion_tokens", 0)),
    }
    return card, usage


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
    return parsed


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
