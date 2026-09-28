from fastapi import FastAPI, APIRouter, UploadFile, File, Form, HTTPException
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import logging
from pathlib import Path
from pydantic import BaseModel, Field
from typing import Optional, List
import uuid
from datetime import datetime, timezone

from ocr_service import extract_payment_details
from gpay_service import gpay_service
from whatsapp_service import whatsapp_service
from bot_orchestrator import process_payment_screenshot

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

mongo_url = os.environ["MONGO_URL"]
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ["DB_NAME"]]

app = FastAPI(title="PayVerify Bot")
api = APIRouter(prefix="/api")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


# ---------- Models ----------
class Settings(BaseModel):
    id: str = Field(default_factory=lambda: "singleton")
    group_name: str = ""
    gpay_email: str = ""
    auto_reply: bool = True
    reply_template_success: str = "✅ Received. UTR ...{utr_last4} ({amount})"
    reply_template_fail: str = "❌ Not received. UTR ...{utr_last4} not found."
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class GPayLoginRequest(BaseModel):
    email: str
    password: str


class VerifyManualRequest(BaseModel):
    utr: str
    amount: Optional[str] = None


class ManualReplyRequest(BaseModel):
    txn_id: str
    reply_text: str


# ---------- Routes ----------
@api.get("/")
async def root():
    return {"service": "PayVerify Bot", "mock_mode": os.environ.get("BOT_MOCK_MODE", "false")}


@api.get("/status")
async def status():
    return {
        "mock_mode": os.environ.get("BOT_MOCK_MODE", "false").lower() == "true",
        "whatsapp_connected": whatsapp_service.is_connected,
        "whatsapp_qr": whatsapp_service.qr_data_url,
        "gpay_logged_in": gpay_service.is_logged_in,
        "gpay_email": gpay_service.email,
    }


@api.get("/settings", response_model=Settings)
async def get_settings():
    doc = await db.settings.find_one({"id": "singleton"}, {"_id": 0})
    if not doc:
        s = Settings()
        await db.settings.insert_one(s.model_dump())
        return s
    return Settings(**doc)


@api.put("/settings", response_model=Settings)
async def update_settings(payload: Settings):
    payload.id = "singleton"
    payload.updated_at = datetime.now(timezone.utc).isoformat()
    await db.settings.update_one(
        {"id": "singleton"}, {"$set": payload.model_dump()}, upsert=True
    )
    return payload


# ---- GPay ----
@api.post("/gpay/login")
async def gpay_login(req: GPayLoginRequest):
    result = await gpay_service.login(req.email, req.password)
    return result


@api.post("/gpay/logout")
async def gpay_logout():
    await gpay_service.close()
    return {"ok": True}


@api.post("/gpay/verify")
async def gpay_verify(req: VerifyManualRequest):
    return await gpay_service.verify_utr(req.utr, req.amount)


# ---- WhatsApp ----
@api.post("/whatsapp/start")
async def wa_start():
    settings = await get_settings()
    if not settings.group_name:
        raise HTTPException(400, "Set group_name in settings first")

    async def on_image(sender: str, image_bytes: bytes, message_id: str):
        await process_payment_screenshot(
            db, image_bytes, sender=sender,
            message_id=message_id, auto_reply=settings.auto_reply,
        )

    return await whatsapp_service.start(settings.group_name, on_image)


@api.post("/whatsapp/stop")
async def wa_stop():
    await whatsapp_service.stop()
    return {"ok": True}


# ---- Screenshot processing (main endpoint used by dashboard + bot) ----
@api.post("/process-screenshot")
async def process_screenshot(
    file: UploadFile = File(...),
    sender: str = Form("manual-upload"),
    auto_reply: bool = Form(False),
):
    """Upload a payment screenshot; runs OCR -> GPay verify -> stores result."""
    if file.content_type not in ("image/png", "image/jpeg", "image/webp", "image/jpg"):
        raise HTTPException(400, f"Unsupported image type: {file.content_type}")
    image_bytes = await file.read()
    if len(image_bytes) > 8 * 1024 * 1024:
        raise HTTPException(400, "Image too large (max 8MB)")
    result = await process_payment_screenshot(
        db, image_bytes, sender=sender, message_id=None, auto_reply=auto_reply
    )
    return result


@api.post("/ocr")
async def ocr_only(file: UploadFile = File(...)):
    """OCR only, no verification."""
    if file.content_type not in ("image/png", "image/jpeg", "image/webp", "image/jpg"):
        raise HTTPException(400, "Unsupported image type")
    image_bytes = await file.read()
    return await extract_payment_details(image_bytes, session_id=str(uuid.uuid4()))


# ---- Transactions ----
@api.get("/transactions")
async def list_transactions(limit: int = 50, status_filter: Optional[str] = None):
    query = {}
    if status_filter:
        query["status"] = status_filter
    docs = await db.transactions.find(query, {"_id": 0}).sort("created_at", -1).to_list(limit)
    return docs


@api.get("/transactions/stats")
async def txn_stats():
    total = await db.transactions.count_documents({})
    received = await db.transactions.count_documents({"status": "received"})
    not_received = await db.transactions.count_documents({"status": "not_received"})
    utr_missing = await db.transactions.count_documents({"status": "utr_not_found"})
    return {
        "total": total,
        "received": received,
        "not_received": not_received,
        "utr_not_found": utr_missing,
    }


@api.get("/transactions/{txn_id}")
async def get_txn(txn_id: str):
    doc = await db.transactions.find_one({"id": txn_id}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Not found")
    return doc


@api.post("/transactions/{txn_id}/mark")
async def mark_transaction(txn_id: str, status: str = Form(...)):
    """Manually override status: received / not_received."""
    if status not in ("received", "not_received", "utr_not_found"):
        raise HTTPException(400, "Invalid status")
    r = await db.transactions.update_one(
        {"id": txn_id},
        {"$set": {"status": status, "manually_overridden": True,
                  "updated_at": datetime.now(timezone.utc).isoformat()}},
    )
    if r.matched_count == 0:
        raise HTTPException(404, "Not found")
    return {"ok": True}


app.include_router(api)
app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get("CORS_ORIGINS", "*").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("shutdown")
async def shutdown():
    client.close()
    await gpay_service.close()
    await whatsapp_service.stop()
