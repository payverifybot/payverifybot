from fastapi import FastAPI, APIRouter, UploadFile, File, Form, HTTPException, Request
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import asyncio
import os
import hmac
import logging
from pathlib import Path
from pydantic import BaseModel, Field
from typing import Optional, List
import uuid
from datetime import datetime, timezone, timedelta

from ocr_service import extract_payment_details
from gpay_service import gpay_service, gpay_pool
from whatsapp_service import whatsapp_service
from bot_orchestrator import process_payment_screenshot
from email_service import send_email, render_digest_html

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

mongo_url = os.environ["MONGO_URL"]
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ["DB_NAME"]]

WEBHOOK_CRON_SECRET = os.environ.get("WEBHOOK_CRON_SECRET", "")

app = FastAPI(title="PayVerify Bot")
api = APIRouter(prefix="/api")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


# ---------- Models ----------
class Settings(BaseModel):
    id: str = Field(default_factory=lambda: "singleton")
    group_name: str = ""                        # legacy single group (kept for backwards compat)
    group_names: List[str] = Field(default_factory=list)  # source of truth for multi-group
    gpay_email: str = ""
    owner_email: str = ""
    owner_name: str = ""
    digest_enabled: bool = False
    auto_reply: bool = True
    reply_template_success: str = "✅ Received via {account_label}. UTR ...{utr_last4} ({amount})"
    reply_template_fail: str = "❌ Not received. UTR ...{utr_last4} not found in any active GPay account."
    reply_template_duplicate: str = "⚠️ Duplicate UTR ...{utr_last4} — already submitted by {orig_sender} on {orig_time}."
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class GPayAccountCreate(BaseModel):
    label: str
    email: str
    password: str


class GPayAccountUpdate(BaseModel):
    label: Optional[str] = None
    password: Optional[str] = None
    is_active: Optional[bool] = None


class GPayLoginRequest(BaseModel):
    email: str
    password: str


class VerifyManualRequest(BaseModel):
    utr: str
    amount: Optional[str] = None


# ---------- Helpers ----------
async def _compute_stats(since_iso: Optional[str] = None, group: Optional[str] = None) -> dict:
    q: dict = {}
    if since_iso:
        q["created_at"] = {"$gte": since_iso}
    if group:
        q["group"] = group
    total = await db.transactions.count_documents(q)
    received = await db.transactions.count_documents({**q, "status": "received"})
    not_received = await db.transactions.count_documents({**q, "status": "not_received"})
    utr_missing = await db.transactions.count_documents({**q, "status": "utr_not_found"})
    duplicate = await db.transactions.count_documents({**q, "status": "duplicate"})
    return {
        "total": total,
        "received": received,
        "not_received": not_received,
        "utr_not_found": utr_missing,
        "duplicate": duplicate,
    }


# ---------- Routes ----------
@api.get("/")
async def root():
    return {"service": "PayVerify Bot", "mock_mode": os.environ.get("BOT_MOCK_MODE", "false")}


@api.get("/status")
async def status():
    accounts = await gpay_pool.list_accounts()
    active_online = [a for a in accounts if a.get("is_active") and a.get("is_logged_in")]
    return {
        "mock_mode": os.environ.get("BOT_MOCK_MODE", "false").lower() == "true",
        "whatsapp_connected": whatsapp_service.is_connected,
        "whatsapp_qr": whatsapp_service.qr_data_url,
        "whatsapp_error": whatsapp_service.last_error,
        "whatsapp_groups": whatsapp_service.group_names,
        "whatsapp_active_group": whatsapp_service.active_group,
        "gpay_logged_in": len(active_online) > 0,
        "gpay_email": (active_online[0]["email"] if active_online else None),
        "gpay_active_count": len(active_online),
        "gpay_total_count": len(accounts),
    }


@api.get("/settings", response_model=Settings)
async def get_settings():
    doc = await db.settings.find_one({"id": "singleton"}, {"_id": 0})
    if not doc:
        s = Settings()
        await db.settings.insert_one(s.model_dump())
        return s
    # Backwards-compat migration: single group_name -> group_names (persist once)
    if not doc.get("group_names") and doc.get("group_name"):
        doc["group_names"] = [doc["group_name"]]
        await db.settings.update_one(
            {"id": "singleton"}, {"$set": {"group_names": doc["group_names"]}}
        )
    return Settings(**doc)


@api.put("/settings", response_model=Settings)
async def update_settings(payload: Settings):
    payload.id = "singleton"
    payload.updated_at = datetime.now(timezone.utc).isoformat()
    # Sync: if only legacy group_name provided, seed group_names; keep both in DB
    if not payload.group_names and payload.group_name:
        payload.group_names = [payload.group_name]
    # And update legacy field to first entry so old readers still work
    if payload.group_names and not payload.group_name:
        payload.group_name = payload.group_names[0]
    # De-dup and strip
    payload.group_names = list({g.strip(): None for g in payload.group_names if g and g.strip()}.keys())
    await db.settings.update_one(
        {"id": "singleton"}, {"$set": payload.model_dump()}, upsert=True
    )
    return payload


# ---- GPay ----
@api.post("/gpay/login")
async def gpay_login(req: GPayLoginRequest):
    """Legacy shortcut: upserts a single default account and logs it in."""
    return await gpay_service.login(req.email, req.password)


@api.post("/gpay/logout")
async def gpay_logout():
    await gpay_service.close()
    return {"ok": True}


@api.post("/gpay/verify")
async def gpay_verify(req: VerifyManualRequest):
    return await gpay_service.verify_utr(req.utr, req.amount)


# ---- GPay Account Pool ----
@api.get("/gpay/accounts")
async def list_gpay_accounts():
    return await gpay_pool.list_accounts()


@api.post("/gpay/accounts")
async def add_gpay_account(payload: GPayAccountCreate):
    return await gpay_pool.add_account(payload.label, payload.email, payload.password)


@api.patch("/gpay/accounts/{account_id}")
async def update_gpay_account(account_id: str, payload: GPayAccountUpdate):
    updates: dict = {}
    if payload.label is not None:
        updates["label"] = payload.label
    if updates:
        await db.gpay_accounts.update_one({"id": account_id}, {"$set": updates})
    if payload.is_active is not None:
        try:
            await gpay_pool.set_active(account_id, payload.is_active)
        except KeyError:
            raise HTTPException(404, "Account not found")
    if payload.password:
        try:
            await gpay_pool.re_login(account_id, password=payload.password)
        except KeyError:
            raise HTTPException(404, "Account not found")
    fresh = await db.gpay_accounts.find_one({"id": account_id}, {"_id": 0, "password_enc": 0})
    if not fresh:
        raise HTTPException(404, "Account not found")
    return fresh


@api.delete("/gpay/accounts/{account_id}")
async def delete_gpay_account(account_id: str):
    return await gpay_pool.delete_account(account_id)


@api.post("/gpay/accounts/{account_id}/hit-limit")
async def gpay_hit_limit(account_id: str):
    """One-click: mark this account as limit-reached (deactivates it)."""
    try:
        await gpay_pool.set_active(account_id, False)
    except KeyError:
        raise HTTPException(404, "Account not found")
    return {"ok": True}


@api.post("/gpay/accounts/{account_id}/reactivate")
async def gpay_reactivate(account_id: str):
    try:
        await gpay_pool.set_active(account_id, True)
    except KeyError:
        raise HTTPException(404, "Account not found")
    return {"ok": True}


@api.post("/gpay/accounts/{account_id}/re-login")
async def gpay_relogin(account_id: str, password: Optional[str] = Form(None)):
    try:
        return await gpay_pool.re_login(account_id, password=password)
    except KeyError:
        raise HTTPException(404, "Account not found")


# ---- WhatsApp ----
@api.post("/whatsapp/start")
async def wa_start():
    settings = await get_settings()
    groups = settings.group_names or ([settings.group_name] if settings.group_name else [])
    if not groups:
        raise HTTPException(400, "Add at least one WhatsApp group in settings first")

    async def on_image(sender: str, image_bytes: bytes, message_id: str, group: str):
        await process_payment_screenshot(
            db, image_bytes, sender=sender,
            message_id=message_id, auto_reply=settings.auto_reply, group=group,
        )

    return await whatsapp_service.start(groups, on_image)


@api.post("/whatsapp/stop")
async def wa_stop():
    await whatsapp_service.stop()
    return {"ok": True}


# ---- Screenshot processing ----
@api.post("/process-screenshot")
async def process_screenshot(
    file: UploadFile = File(...),
    sender: str = Form("manual-upload"),
    group: Optional[str] = Form(None),
    auto_reply: bool = Form(False),
):
    if file.content_type not in ("image/png", "image/jpeg", "image/webp", "image/jpg"):
        raise HTTPException(400, f"Unsupported image type: {file.content_type}")
    image_bytes = await file.read()
    if len(image_bytes) > 8 * 1024 * 1024:
        raise HTTPException(400, "Image too large (max 8MB)")
    return await process_payment_screenshot(
        db, image_bytes, sender=sender, message_id=None,
        auto_reply=auto_reply, group=group,
    )


@api.post("/ocr")
async def ocr_only(file: UploadFile = File(...)):
    if file.content_type not in ("image/png", "image/jpeg", "image/webp", "image/jpg"):
        raise HTTPException(400, "Unsupported image type")
    image_bytes = await file.read()
    return await extract_payment_details(image_bytes, session_id=str(uuid.uuid4()))


# ---- Transactions ----
@api.get("/transactions")
async def list_transactions(limit: int = 50, status_filter: Optional[str] = None, group: Optional[str] = None):
    q: dict = {}
    if status_filter:
        q["status"] = status_filter
    if group:
        q["group"] = group
    return await db.transactions.find(q, {"_id": 0}).sort("created_at", -1).to_list(limit)


@api.get("/transactions/stats")
async def txn_stats(group: Optional[str] = None):
    return await _compute_stats(group=group)


@api.get("/transactions/groups")
async def txn_groups():
    """List distinct groups seen in transactions, with counts."""
    pipeline = [
        {"$match": {"group": {"$ne": None}}},
        {"$group": {"_id": "$group", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
        {"$project": {"_id": 0, "group": "$_id", "count": 1}},
    ]
    return await db.transactions.aggregate(pipeline).to_list(50)


@api.get("/transactions/{txn_id}")
async def get_txn(txn_id: str):
    doc = await db.transactions.find_one({"id": txn_id}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Not found")
    return doc


@api.post("/transactions/{txn_id}/mark")
async def mark_transaction(txn_id: str, status: str = Form(...)):
    if status not in ("received", "not_received", "utr_not_found", "duplicate"):
        raise HTTPException(400, "Invalid status")
    r = await db.transactions.update_one(
        {"id": txn_id},
        {"$set": {"status": status, "manually_overridden": True,
                  "updated_at": datetime.now(timezone.utc).isoformat()}},
    )
    if r.matched_count == 0:
        raise HTTPException(404, "Not found")
    return {"ok": True}


# ---- Daily Digest ----
async def _send_daily_digest() -> dict:
    settings_doc = await db.settings.find_one({"id": "singleton"}, {"_id": 0}) or {}
    owner_email = (settings_doc.get("owner_email") or "").strip()
    if not settings_doc.get("digest_enabled") or not owner_email:
        return {"sent": False, "reason": "digest disabled or owner_email missing"}
    since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    stats = await _compute_stats(since_iso=since)
    recent = await db.transactions.find(
        {"created_at": {"$gte": since}}, {"_id": 0}
    ).sort("created_at", -1).to_list(50)
    html = render_digest_html(stats, recent, owner_name=settings_doc.get("owner_name") or "there")
    email_id = await send_email(
        to=owner_email,
        subject=f"Daily payment digest — {stats['received']} received, {stats['not_received']} not received",
        html=html,
    )
    await db.digests.insert_one({
        "id": str(uuid.uuid4()),
        "sent_at": datetime.now(timezone.utc).isoformat(),
        "to": owner_email,
        "email_id": email_id,
        "stats": stats,
    })
    return {"sent": True, "email_id": email_id, "stats": stats, "to": owner_email}


@api.post("/digest/send-now")
async def digest_send_now():
    """Manual trigger from the dashboard."""
    return await _send_daily_digest()


@api.post("/cron/digest")
async def cron_digest(request: Request):
    # Cron endpoints must ack 2xx immediately; enqueue/background the actual work.
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer ") or not WEBHOOK_CRON_SECRET:
        raise HTTPException(401, "unauthorized")
    token = auth.split(" ", 1)[1]
    if not hmac.compare_digest(token, WEBHOOK_CRON_SECRET):
        raise HTTPException(401, "unauthorized")

    run_id = request.headers.get("x-webhook-id") or str(uuid.uuid4())
    if await db.cron_runs.find_one({"run_id": run_id}):
        return {"ok": True, "duplicate": True}
    await db.cron_runs.insert_one({
        "run_id": run_id,
        "kind": "digest",
        "received_at": datetime.now(timezone.utc).isoformat(),
    })
    # Background the actual send so we ack 2xx fast
    asyncio.create_task(_send_daily_digest())
    return {"ok": True, "run_id": run_id}


@api.get("/digest/history")
async def digest_history(limit: int = 20):
    return await db.digests.find({}, {"_id": 0}).sort("sent_at", -1).to_list(limit)


app.include_router(api)
app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get("CORS_ORIGINS", "*").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- Serve the built React bundle when it exists (local runner) ---------
# In the Emergent preview the frontend runs as a separate service on :3000
# so this block silently no-ops. In the local docker-runner the Dockerfile
# builds the React app into /app/frontend/build and this mounts it at /.
FRONTEND_DIR = Path("/app/frontend/build")
if FRONTEND_DIR.exists():
    from fastapi.staticfiles import StaticFiles
    from fastapi.responses import FileResponse

    app.mount(
        "/static",
        StaticFiles(directory=str(FRONTEND_DIR / "static")),
        name="static",
    )

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str):
        # Never intercept /api/* — the router above already handled those.
        if full_path.startswith("api/") or full_path == "api":
            raise HTTPException(status_code=404)
        candidate = FRONTEND_DIR / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(FRONTEND_DIR / "index.html")


@app.on_event("startup")
async def startup():
    gpay_pool.bind_db(db)
    asyncio.create_task(gpay_pool.restore_from_db())


@app.on_event("shutdown")
async def shutdown():
    client.close()
    await gpay_pool.close_all()
    await whatsapp_service.stop()
