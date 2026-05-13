#!/usr/bin/env python3
"""Sign and POST a Hermes/agent bundle to the Cathedral publisher (ssh-probe tier).

Uses cathedral.auth.hotkey_signature (canonical JSON + sr25519).

Prefer: ./scripts/miner/submit_agent_bundle.sh (sets venv + PYTHONPATH). Example:
  ./scripts/pack_baseline_bundle.sh
  ./scripts/miner/submit_agent_bundle.sh --bundle .../cathedral-baseline-bundle.zip \\
    --wallet-name NAME --wallet-hotkey HOTKEY --card-id eu-ai-act \\
    --display-name LABEL --ssh-host HOST --ssh-user cathedral-probe

Manual: after ``source .venv/bin/activate`` use **&&** before ``PYTHONPATH=src python ...``
(never glue ``activate`` and ``PYTHONPATH`` on one line).

Miner host: run scripts/miner/verify_cathedral_probe.sh as ssh_user before submitting.

Loop: ``--loop`` sleeps ``--interval-secs`` (default **60**) between tries. Unless
``--submit-unchanged``, identical bundle bytes skip POST to avoid duplicate 409s.
"""

from __future__ import annotations

import argparse
import base64
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import blake3
import httpx

from cathedral.auth.hotkey_signature import canonical_claim_bytes


def _now_iso_ms_z() -> str:
    now = datetime.now(UTC)
    ms = (now.microsecond // 1000) * 1000
    now = now.replace(microsecond=ms)
    return now.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _ts() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _load_wallet():
    try:
        import bittensor as bt
    except ImportError as e:
        raise SystemExit("Install cathedral with bittensor: pip install -e .") from e
    return bt


def _run_pack(pack_command: str) -> None:
    # Operator-controlled shell (e.g. path to pack_baseline_bundle.sh).
    subprocess.run(["/bin/bash", "-lc", pack_command], check=True)  # noqa: S603


def _post_submit(
    *,
    raw: bytes,
    wallet: object,
    hk: str,
    url: str,
    data: dict[str, str],
    client: httpx.Client,
) -> httpx.Response:
    bundle_hash = blake3.blake3(raw).hexdigest()
    submitted_at = _now_iso_ms_z()
    payload = canonical_claim_bytes(
        bundle_hash=bundle_hash,
        card_id=data["card_id"],
        miner_hotkey=hk,
        submitted_at=submitted_at,
    )
    sig_bytes: bytes = wallet.hotkey.sign(payload)  # type: ignore[union-attr]
    sig_b64 = base64.b64encode(sig_bytes).decode("ascii")
    data = {**data, "submitted_at": submitted_at}
    headers = {
        "X-Cathedral-Hotkey": hk,
        "X-Cathedral-Signature": sig_b64,
    }
    files = {"bundle": ("bundle.zip", raw, "application/zip")}
    return client.post(url, headers=headers, data=data, files=files)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--bundle", type=Path, required=True, help="Path to agent zip")
    p.add_argument("--publisher-url", default="https://api.cathedral.computer")
    p.add_argument("--card-id", required=True)
    p.add_argument("--display-name", required=True)
    p.add_argument("--wallet-name", required=True)
    p.add_argument("--wallet-hotkey", required=True)
    p.add_argument("--ssh-host", required=True)
    p.add_argument("--ssh-user", required=True)
    p.add_argument("--ssh-port", type=int, default=22)
    p.add_argument("--bio", default=None)
    p.add_argument(
        "--loop",
        action="store_true",
        help="Run forever: optional --pack-command, then submit, sleep --interval-secs.",
    )
    p.add_argument(
        "--interval-secs",
        type=int,
        default=60,
        metavar="N",
        help="Sleep between iterations when --loop (default 60 = 1 minute).",
    )
    p.add_argument(
        "--pack-command",
        default=None,
        metavar="SHELL",
        help="Run via bash -lc before each read of --bundle (e.g. pack_baseline_bundle.sh).",
    )
    p.add_argument(
        "--submit-unchanged",
        action="store_true",
        help="With --loop, POST even when bundle_hash matches the previous iteration "
        "(default: skip POST to avoid 409 duplicate spam).",
    )
    args = p.parse_args()

    bt = _load_wallet()
    wallet = bt.Wallet(name=args.wallet_name, hotkey=args.wallet_hotkey)
    hk = wallet.hotkey.ss58_address

    base = args.publisher_url.rstrip("/")
    url = f"{base}/v1/agents/submit"

    data: dict[str, str] = {
        "card_id": args.card_id,
        "display_name": args.display_name[:64],
        "attestation_mode": "ssh-probe",
        "ssh_host": args.ssh_host,
        "ssh_user": args.ssh_user,
        "ssh_port": str(args.ssh_port),
    }
    if args.bio is not None:
        data["bio"] = args.bio[:280]

    if args.loop and args.interval_secs < 1:
        print("--interval-secs must be >= 1", file=sys.stderr)
        return 1

    last_bundle_hash: str | None = None
    while True:
        if args.pack_command:
            _run_pack(args.pack_command)

        raw = args.bundle.read_bytes()
        bundle_hash = blake3.blake3(raw).hexdigest()

        if (
            args.loop
            and last_bundle_hash is not None
            and bundle_hash == last_bundle_hash
            and not args.submit_unchanged
        ):
            print(
                f"{_ts()} skip POST bundle_hash={bundle_hash[:16]}... "
                "(unchanged since last pack; edit sources or use --submit-unchanged)"
            )
            time.sleep(args.interval_secs)
            continue

        with httpx.Client(timeout=120.0) as client:
            r = _post_submit(raw=raw, wallet=wallet, hk=hk, url=url, data=data, client=client)

        last_bundle_hash = bundle_hash
        print(f"{_ts()} {r.status_code} bundle_hash={bundle_hash[:16]}... {r.text}")

        if r.status_code == 202:
            if not args.loop:
                return 0
        elif r.status_code == 409:
            detail = ""
            try:
                j = r.json()
                if isinstance(j, dict):
                    detail = str(j.get("detail", ""))
            except ValueError:
                detail = ""
            dup = "duplicate" in detail.lower()
            if dup:
                if args.loop:
                    pass
                else:
                    print(
                        "\nNote: This hotkey already submitted this exact zip for this card_id "
                        "(same bundle_hash). Change the bundle bytes (e.g. edit soul.md) or use "
                        "another card_id to submit again; the first submission is still valid.",
                        file=sys.stderr,
                    )
                    return 1
            else:
                return 1
        else:
            return 1

        if not args.loop:
            break
        time.sleep(args.interval_secs)

    return 0


if __name__ == "__main__":
    sys.exit(main())
