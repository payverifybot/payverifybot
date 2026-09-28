"""OCR service for extracting UTR and amount from payment screenshots
using Gemini vision via emergentintegrations."""
import os
import re
import json
import base64
import logging
import tempfile
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv
from emergentintegrations.llm.chat import (
    LlmChat,
    UserMessage,
    FileContentWithMimeType,
)

load_dotenv(Path(__file__).parent / ".env")

logger = logging.getLogger(__name__)

EMERGENT_LLM_KEY = os.environ.get("EMERGENT_LLM_KEY")

SYSTEM_PROMPT = """You are an expert at reading Indian UPI/GPay/PhonePe/Paytm
payment receipt screenshots. Extract the following fields precisely:

- utr: The UTR / UPI Reference No / Transaction ID (usually a 12-digit number,
  labeled UTR / UPI Ref No / Txn ID / Reference number). Return only digits.
- amount: The payment amount (rupees, no symbol). E.g. "1500" or "1500.00".
- payer_name: Name of the person who sent money (if visible).
- payee_name: Name of the merchant/receiver (if visible).
- payer_upi: UPI id of the payer (if visible).
- payee_upi: UPI id of the payee (if visible).
- timestamp: Date & time of payment as shown on the receipt.
- status: "success" if the screenshot clearly shows a successful payment,
  "failed" if failed, "unknown" otherwise.

Reply with STRICT JSON only, no markdown, no commentary. Missing fields → null.
"""


def _guess_mime(image_bytes: bytes) -> str:
    if image_bytes[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if image_bytes[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if image_bytes[:4] == b"RIFF" and image_bytes[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


def _extract_json(text: str) -> dict:
    text = text.strip()
    # strip code fences
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except Exception:
        # Find first {..} block
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            return json.loads(m.group(0))
        raise


async def extract_payment_details(image_bytes: bytes, session_id: str) -> dict:
    """Run Gemini vision on payment screenshot; return dict of fields."""
    if not EMERGENT_LLM_KEY:
        raise RuntimeError("EMERGENT_LLM_KEY not configured")

    mime = _guess_mime(image_bytes)
    suffix = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}[mime]

    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(image_bytes)
        tmp_path = tmp.name

    try:
        chat = LlmChat(
            api_key=EMERGENT_LLM_KEY,
            session_id=session_id,
            system_message=SYSTEM_PROMPT,
        ).with_model("gemini", "gemini-3-flash-preview")

        img = FileContentWithMimeType(file_path=tmp_path, mime_type=mime)
        msg = UserMessage(
            text="Extract the payment fields from this receipt as JSON.",
            file_contents=[img],
        )
        raw = await chat.send_message(msg)
        logger.info("OCR raw response: %s", raw[:400])
        data = _extract_json(raw)

        # Normalize
        if data.get("utr"):
            data["utr"] = re.sub(r"\D", "", str(data["utr"]))
        if data.get("amount") is not None:
            data["amount"] = str(data["amount"]).replace(",", "").strip()
        data["utr_last4"] = data["utr"][-4:] if data.get("utr") else None
        return data
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


def extract_utr_from_text(text: str) -> Optional[str]:
    """Fallback: pull a 12-digit UTR from a text blob."""
    if not text:
        return None
    m = re.search(r"\b(\d{12})\b", text)
    return m.group(1) if m else None
