"""Bot orchestrator — glues OCR + duplicate check + GPay verification + WhatsApp reply."""
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional
from ocr_service import extract_payment_details
from gpay_service import gpay_service
from whatsapp_service import whatsapp_service

logger = logging.getLogger(__name__)


async def _find_duplicate(db, utr: str) -> Optional[dict]:
    """Return the earliest existing transaction with the same UTR, if any."""
    if not utr:
        return None
    return await db.transactions.find_one(
        {"utr": utr, "status": {"$in": ["received", "not_received"]}},
        {"_id": 0},
        sort=[("created_at", 1)],
    )


async def process_payment_screenshot(
    db,
    image_bytes: bytes,
    sender: str = "unknown",
    message_id: Optional[str] = None,
    auto_reply: bool = True,
) -> dict:
    """Full pipeline: OCR -> duplicate check -> GPay verify -> WhatsApp reply -> persist."""
    txn_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    ocr: dict = {}
    verify: dict = {}
    reply_text = ""
    status = "processing"
    duplicate_of: Optional[str] = None

    try:
        ocr = await extract_payment_details(image_bytes, session_id=txn_id) or {}
    except Exception as e:
        logger.exception("OCR failed")
        ocr = {"error": str(e)}

    utr = ocr.get("utr") if isinstance(ocr, dict) else None
    amount = ocr.get("amount") if isinstance(ocr, dict) else None

    # Load reply templates from settings once
    settings = await db.settings.find_one({"id": "singleton"}, {"_id": 0}) or {}
    tpl_ok = settings.get("reply_template_success") or "✅ Received. UTR ...{utr_last4} ({amount})"
    tpl_fail = settings.get("reply_template_fail") or "❌ Not received. UTR ...{utr_last4} not found."
    tpl_dup = settings.get("reply_template_duplicate") or "⚠️ Duplicate UTR ...{utr_last4} — already submitted by {orig_sender} on {orig_time}."

    if not utr:
        status = "utr_not_found"
        reply_text = "Could not read UTR from screenshot. Please resend a clearer screenshot."
    else:
        # Duplicate guard — check before touching GPay
        dup = await _find_duplicate(db, utr)
        if dup:
            status = "duplicate"
            duplicate_of = dup["id"]
            try:
                orig_dt = datetime.fromisoformat(dup["created_at"]).strftime("%d %b %H:%M")
            except Exception:
                orig_dt = dup.get("created_at", "earlier")
            ctx = {
                "utr_last4": utr[-4:], "amount": amount or "?", "utr": utr,
                "orig_sender": dup.get("sender") or "someone", "orig_time": orig_dt,
            }
            try:
                reply_text = tpl_dup.format(**ctx)
            except Exception:
                reply_text = f"⚠️ Duplicate UTR ...{utr[-4:]} — already submitted."
            verify = {"skipped": True, "reason": "duplicate", "duplicate_of": duplicate_of}
        else:
            verify = await gpay_service.verify_utr(utr=utr, amount=amount) or {}
            ctx = {"utr_last4": utr[-4:], "amount": amount or "?", "utr": utr}
            try:
                if verify.get("found"):
                    status = "received"
                    reply_text = tpl_ok.format(**ctx)
                else:
                    status = "not_received"
                    reply_text = tpl_fail.format(**ctx)
            except Exception:
                reply_text = (
                    f"✅ Received. UTR ...{utr[-4:]}" if verify.get("found")
                    else f"❌ Not received. UTR ...{utr[-4:]}"
                )
                status = "received" if verify.get("found") else "not_received"

    replied = False
    if auto_reply and message_id:
        try:
            replied = await whatsapp_service.send_reply(message_id, reply_text)
        except Exception:
            logger.exception("WA reply failed")

    doc = {
        "id": txn_id,
        "created_at": now,
        "sender": sender,
        "message_id": message_id,
        "status": status,
        "utr": utr,
        "utr_last4": utr[-4:] if utr else None,
        "amount": amount,
        "payer_name": ocr.get("payer_name") if isinstance(ocr, dict) else None,
        "ocr_raw": ocr,
        "gpay_result": verify,
        "reply_text": reply_text,
        "replied": replied,
        "duplicate_of": duplicate_of,
    }
    await db.transactions.insert_one({**doc})
    doc.pop("_id", None)
    return doc
