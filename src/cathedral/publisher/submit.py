"""POST /v1/agents/submit handler.

Flow (CONTRACTS.md Section 6 step 1 + 2):

    multipart upload
       │
       ▼
    auth header dep (X-Cathedral-Hotkey, X-Cathedral-Signature)
       │
       ▼
    bundle bytes -> blake3 = bundle_hash (SERVER-COMPUTED)
       │
       ▼
    verify hotkey signature against canonical_json({bundle_hash,
                                                    card_id, hotkey,
                                                    submitted_at})
       │   - signature mismatch       -> 401
       │   - card_id unknown          -> 404
       │   - bundle structure invalid -> 422
       │   - bundle too large         -> 400
       ▼
    similarity check (Section 7.1)
       │   - duplicate                -> 409
       ▼
    encrypt bundle -> Hippius PUT
       │   - storage failure          -> 503 (don't write DB row)
       ▼
    INSERT agent_submissions row, status='queued'
       │   - UNIQUE violation         -> 409
       ▼
    202 Accepted { id, bundle_hash, status, submitted_at }
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

import aiosqlite
import blake3
import structlog
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status

from cathedral.auth import InvalidSignatureError, verify_hotkey_signature
from cathedral.publisher import repository, similarity
from cathedral.publisher.auth_signature import HotkeyAuth, hotkey_auth_header
from cathedral.publisher.similarity import SimilarityRejection
from cathedral.storage import (
    BundleStructureError,
    BundleTooLargeError,
    EncryptionError,
    HippiusError,
    encrypt_bundle,
    validate_hermes_bundle,
)

if TYPE_CHECKING:
    from cathedral.publisher.app import PublisherContext

logger = structlog.get_logger(__name__)


_MAX_BUNDLE_BYTES = 10 * 1024 * 1024
_MAX_LOGO_BYTES = 200 * 1024
_ALLOWED_LOGO_TYPES = {"image/png", "image/jpeg", "image/webp"}
_LOGO_EXT_BY_TYPE = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}
_MAX_DISPLAY_NAME = 64
_MAX_BIO = 280


@dataclass(frozen=True)
class SubmissionResponse:
    id: str
    bundle_hash: str
    status: str
    submitted_at: str


router = APIRouter()


@router.post("/v1/agents/submit", status_code=status.HTTP_202_ACCEPTED)
async def submit_agent(
    request: Request,
    bundle: UploadFile = File(...),
    card_id: str = Form(...),
    display_name: str = Form(...),
    bio: str | None = Form(default=None),
    # CRIT-1: `submitted_at` MUST come from the server clock. We accept
    # the form field for backward-compat with existing miners but IGNORE
    # its value — the server-generated timestamp is always authoritative.
    # The miner must sign over the server-issued timestamp returned in the
    # response (or call the prospective /v1/server-time endpoint).
    submitted_at_form: str | None = Form(default=None, alias="submitted_at"),
    logo: UploadFile | None = File(default=None),
    auth: HotkeyAuth = Depends(hotkey_auth_header),
) -> dict[str, str]:
    ctx: PublisherContext = request.app.state.ctx

    if ctx.submissions_paused:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="submissions paused",
        )

    # ----- form validation ---------------------------------------------
    display_name = display_name.strip()
    if not display_name:
        raise HTTPException(status_code=400, detail="display_name required")
    if len(display_name) > _MAX_DISPLAY_NAME:
        raise HTTPException(
            status_code=400,
            detail=f"display_name exceeds {_MAX_DISPLAY_NAME} chars",
        )
    if bio is not None and len(bio) > _MAX_BIO:
        raise HTTPException(
            status_code=400, detail=f"bio exceeds {_MAX_BIO} chars"
        )

    if not bundle.filename or not bundle.filename.lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="bundle must be a .zip file")

    # ----- bundle bytes ------------------------------------------------
    raw = await _read_capped(bundle, _MAX_BUNDLE_BYTES + 1)
    if len(raw) > _MAX_BUNDLE_BYTES:
        # CONTRACTS.md L6: oversized bundle returns 413 Payload Too Large.
        raise HTTPException(
            status_code=413,
            detail=f"bundle exceeds {_MAX_BUNDLE_BYTES // (1024 * 1024)} MiB limit",
        )

    try:
        validate_hermes_bundle(raw)
    except BundleTooLargeError as e:
        raise HTTPException(status_code=413, detail=str(e)) from e
    except BundleStructureError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    bundle_hash = blake3.blake3(raw).hexdigest()

    # ----- card definition --------------------------------------------
    card_def = await repository.get_card_definition(ctx.db, card_id)
    if card_def is None:
        raise HTTPException(status_code=404, detail=f"card not found: {card_id}")
    if card_def["status"] != "active":
        raise HTTPException(
            status_code=404, detail=f"card not active: {card_id}"
        )

    # ----- timestamp + signature verify --------------------------------
    # CONTRACTS.md §4.1 + CRIT-1: server clock is the SOLE source of truth
    # for `submitted_at` recorded in agent_submissions and propagated to
    # `first_mover_at`. The client-supplied form field (if any) is used
    # ONLY to verify the hotkey signature — its value is NEVER persisted
    # and NEVER used for first-mover anchoring. A backdated client value
    # may still verify the signature, but it loses the first-mover race
    # to whoever actually arrived at the server first. Additionally, a
    # client value too far from server time is rejected outright to close
    # the obvious far-future / far-past window without relying on the
    # downstream first-mover comparison alone.
    server_submitted_at = datetime.now(UTC)
    server_submitted_at_iso = _ms_iso(server_submitted_at)

    if submitted_at_form:
        try:
            client_submitted_at = datetime.fromisoformat(
                submitted_at_form.replace("Z", "+00:00")
            )
        except ValueError as e:
            raise HTTPException(
                status_code=400, detail="submitted_at must be ISO-8601"
            ) from e
        if client_submitted_at.tzinfo is None:
            client_submitted_at = client_submitted_at.replace(tzinfo=UTC)
        # ±5 minute clock-skew window — anything wider is considered a
        # backdating / forward-dating attempt.
        skew_secs = abs(
            (server_submitted_at - client_submitted_at).total_seconds()
        )
        if skew_secs > 300:
            logger.info(
                "submission_clock_skew",
                hotkey=auth.hotkey_ss58,
                skew_secs=skew_secs,
            )
            raise HTTPException(
                status_code=400,
                detail="submitted_at outside acceptable clock-skew window",
            )
        signed_submitted_at_iso = submitted_at_form
    else:
        signed_submitted_at_iso = server_submitted_at_iso

    try:
        verify_hotkey_signature(
            hotkey_ss58=auth.hotkey_ss58,
            signature_b64=auth.signature_b64,
            bundle_hash=bundle_hash,
            card_id=card_id,
            submitted_at=signed_submitted_at_iso,
        )
    except InvalidSignatureError as e:
        logger.info("submission_sig_failed", hotkey=auth.hotkey_ss58)
        raise HTTPException(
            status_code=401, detail="invalid hotkey signature"
        ) from e
    _ = request  # keep for trace-id middleware compatibility

    # Bind the persisted timestamp to the server clock — this is what
    # `first_mover_at` and the response carry. The client's value (if any)
    # was used solely to verify the signature.
    submitted_at = server_submitted_at
    submitted_at_iso = server_submitted_at_iso

    # ----- similarity check --------------------------------------------
    # CONTRACTS.md L7: similarity rejection returns 202 with status=rejected
    # + rejection_reason; EXCEPT for "exact bundle duplicate" which the
    # contract test pins as 409 (cross-hotkey same-bundle) and 409 for
    # same-hotkey duplicate (idx_agent_unique). Other similarity reasons
    # (fingerprint, fuzzy display name) go through the 202+rejected path.
    rejection_reason: str | None = None
    sim_metadata_fingerprint: str
    sim_display_name_norm: str
    try:
        sim = await similarity.run_similarity_check(
            ctx.db,
            miner_hotkey=auth.hotkey_ss58,
            card_id=card_id,
            display_name=display_name,
            bundle_hash=bundle_hash,
            bundle_size_bytes=len(raw),
        )
        sim_metadata_fingerprint = sim.metadata_fingerprint
        sim_display_name_norm = sim.display_name_norm
        _ = sim_display_name_norm  # reserved for future use
    except SimilarityRejection as e:
        msg = str(e)
        # "duplicate submission" = same hotkey same bundle (covered by UNIQUE
        # index too); "exact bundle duplicate" = cross-hotkey. Both 409.
        if msg in {"duplicate submission", "exact bundle duplicate"}:
            raise HTTPException(status_code=409, detail=msg) from e
        # Other similarity rejections: 202 + status=rejected.
        rejection_reason = msg
        sim_metadata_fingerprint = similarity.metadata_fingerprint(
            display_name=display_name, bundle_size_bytes=len(raw)
        )
        sim_display_name_norm = similarity.normalize_display_name(display_name)

    # ----- logo upload (optional, before bundle so we can reference URL) -
    submission_id = str(uuid4())
    logo_url: str | None = None

    # If similarity check rejected (non-exact-duplicate path), persist the
    # row with status=rejected and return 202 with rejection_reason. Skip
    # bundle upload + first-mover anchor.
    if rejection_reason is not None:
        await repository.insert_agent_submission(
            ctx.db,
            id=submission_id,
            miner_hotkey=auth.hotkey_ss58,
            card_id=card_id,
            bundle_blob_key="",
            bundle_hash=bundle_hash,
            bundle_size_bytes=len(raw),
            encryption_key_id="",
            bundle_signature=auth.signature_b64,
            display_name=display_name,
            bio=bio,
            logo_url=None,
            soul_md_preview=None,
            metadata_fingerprint=sim_metadata_fingerprint,
            similarity_check_passed=False,
            rejection_reason=rejection_reason,
            status="rejected",
            submitted_at=submitted_at,
            submitted_at_iso=submitted_at_iso,
            first_mover_at=None,
        )
        logger.info(
            "submission_rejected_similarity",
            submission_id=submission_id,
            hotkey=auth.hotkey_ss58,
            card_id=card_id,
            reason=rejection_reason,
        )
        return {
            "id": submission_id,
            "bundle_hash": bundle_hash,
            "status": "rejected",
            "rejection_reason": rejection_reason,
            "submitted_at": submitted_at_iso,
        }

    if logo is not None and logo.filename:
        if logo.content_type not in _ALLOWED_LOGO_TYPES:
            raise HTTPException(
                status_code=400,
                detail=f"logo content type not allowed: {logo.content_type}",
            )
        logo_bytes = await _read_capped(logo, _MAX_LOGO_BYTES + 1)
        if len(logo_bytes) > _MAX_LOGO_BYTES:
            raise HTTPException(
                status_code=400,
                detail=f"logo exceeds {_MAX_LOGO_BYTES // 1024} KiB limit",
            )
        try:
            logo_url = await ctx.hippius.put_logo(
                submission_id,
                logo_bytes,
                content_type=logo.content_type or "image/png",
                ext=_LOGO_EXT_BY_TYPE.get(logo.content_type or "", "png"),
            )
        except HippiusError as e:
            logger.warning("logo_upload_failed", error=str(e))
            # Logo is optional; don't fail the whole submission.
            logo_url = None

    # ----- encrypt + upload bundle -------------------------------------
    try:
        encrypted = encrypt_bundle(raw)
    except EncryptionError as e:
        logger.error("encrypt_failed", error=str(e))
        raise HTTPException(
            status_code=500, detail="bundle encryption failed"
        ) from e

    try:
        blob_key = await ctx.hippius.put_bundle(
            submission_id,
            encrypted.ciphertext,
            bundle_hash_hex=bundle_hash,
        )
    except HippiusError as e:
        logger.warning("bundle_upload_failed", error=str(e))
        raise HTTPException(
            status_code=503, detail="bundle storage unavailable"
        ) from e

    # ----- first-mover anchor -----------------------------------------
    existing_first = await repository.first_mover_for_fingerprint(
        ctx.db, card_id, sim_metadata_fingerprint
    )
    first_mover_at = (
        existing_first["first_mover_at"]
        if existing_first
        else submitted_at_iso
    )
    if isinstance(first_mover_at, str):
        try:
            first_mover_dt = datetime.fromisoformat(
                first_mover_at.replace("Z", "+00:00")
            )
        except ValueError:
            first_mover_dt = submitted_at
    else:
        first_mover_dt = first_mover_at  # type: ignore[unreachable]

    # ----- INSERT -----------------------------------------------------
    try:
        await repository.insert_agent_submission(
            ctx.db,
            id=submission_id,
            miner_hotkey=auth.hotkey_ss58,
            card_id=card_id,
            bundle_blob_key=blob_key,
            bundle_hash=bundle_hash,
            bundle_size_bytes=len(raw),
            encryption_key_id=encrypted.encryption_key_id,
            bundle_signature=auth.signature_b64,
            display_name=display_name,
            bio=bio,
            logo_url=logo_url,
            soul_md_preview=None,  # populated post-decrypt during eval
            metadata_fingerprint=sim_metadata_fingerprint,
            similarity_check_passed=True,
            rejection_reason=None,
            status="queued",
            submitted_at=submitted_at,
            submitted_at_iso=submitted_at_iso,
            first_mover_at=first_mover_dt,
        )
    except aiosqlite.IntegrityError as e:
        # idx_agent_unique violation = same hotkey + same card + same bundle.
        # Best-effort cleanup of the freshly uploaded blob.
        try:
            await ctx.hippius.delete_bundle(blob_key)
        except HippiusError:
            pass
        raise HTTPException(status_code=409, detail="duplicate submission") from e

    logger.info(
        "submission_accepted",
        submission_id=submission_id,
        hotkey=auth.hotkey_ss58,
        card_id=card_id,
        bundle_hash=bundle_hash,
        size=len(raw),
    )
    return {
        "id": submission_id,
        "bundle_hash": bundle_hash,
        "status": "pending_check",  # contract returns the soft state
        "submitted_at": submitted_at_iso,
    }


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


async def _read_capped(upload: UploadFile, cap: int) -> bytes:
    """Read up to `cap` bytes from a SpooledTemporaryFile-backed upload.

    Reads in 64 KiB chunks so we never accidentally pull a multi-GiB
    body into memory if the client lies about content-length.
    """
    out = bytearray()
    while True:
        chunk = await upload.read(64 * 1024)
        if not chunk:
            break
        out.extend(chunk)
        if len(out) > cap:
            break
    return bytes(out)


def _ms_iso(dt: datetime) -> str:
    """ISO-8601 UTC with millisecond precision and trailing `Z`."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    s = dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}"
    return s + "Z"
