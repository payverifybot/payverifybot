"""Bot orchestrator — glues OCR + GPay verification + WhatsApp reply."""
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional
from ocr_service import extract_payment_details
from gpay_service import gpay_service
from whatsapp_service import whatsapp_service

logger = logging.getLogger(__name__)


async def process_payment_screenshot(
    db,
    image_bytes: bytes,
    sender: str = "unknown",
    message_id: Optional[str] = None,
    auto_reply: bool = True,
) -> dict:
    """Full pipeline: OCR -> GPay verify -> WhatsApp reply -> persist."""
    txn_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    ocr = {}
    verify = {}
    reply_text = ""
    status = "processing"

    try:
        ocr = await extract_payment_details(image_bytes, session_id=txn_id)
    except Exception as e:
        logger.exception("OCR failed")
        ocr = {"error": str(e)}

    utr = ocr.get("utr") if isinstance(ocr, dict) else None
    amount = ocr.get("amount") if isinstance(ocr, dict) else None

    if not utr:
        status = "utr_not_found"
        reply_text = "Could not read UTR from screenshot. Please resend a clearer screenshot."
    else:
        verify = await gpay_service.verify_utr(utr=utr, amount=amount)
        if verify.get("found"):
            status = "received"
            reply_text = f"✅ Received. UTR ...{utr[-4:]}"
        else:
            status = "not_received"
            reply_text = f"❌ Not received. UTR ...{utr[-4:]} not found in GPay Business."

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
    }
    await db.transactions.insert_one({**doc})
    doc.pop("_id", None)
    return doc
