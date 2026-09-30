"""GPay Business — multi-account pool.

Each account has its own Playwright-driven browser session against
business.google.com/payments. Verification checks the UTR across ALL
currently-active accounts in parallel (asyncio.gather) — the first one that
finds it wins.

Passwords are stored encrypted (Fernet) so accounts survive backend restarts
without re-entry. In MOCK MODE we skip Playwright entirely: even-last-digit UTR
is considered "found" by the first active account.
"""
import os
import uuid
import asyncio
import logging
from pathlib import Path
from typing import Optional
from datetime import datetime, timezone
from dotenv import load_dotenv
from cryptography.fernet import Fernet, InvalidToken
import runtime_state

load_dotenv(Path(__file__).parent / ".env")
logger = logging.getLogger(__name__)

GPAY_URL = "https://business.google.com/payments"
BASE_PROFILE_DIR = Path(os.environ.get("GPAY_PROFILE_DIR", "/tmp/gpay_profiles"))
ENC_KEY = os.environ.get("GPAY_ENC_KEY")


def _fernet() -> Optional[Fernet]:
    if not ENC_KEY:
        return None
    try:
        return Fernet(ENC_KEY.encode())
    except Exception:
        logger.exception("Invalid GPAY_ENC_KEY")
        return None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_account(doc: dict) -> dict:
    """Return an account dict without the password ciphertext."""
    return {k: v for k, v in doc.items() if k not in ("password_enc", "_id")}


class _AccountSession:
    """Playwright session for ONE GPay Business account."""

    def __init__(self, account_id: str, email: str):
        self.account_id = account_id
        self.email = email
        self._playwright = None
        self._browser = None
        self._page = None
        self.is_logged_in = False
        self.last_error: Optional[str] = None

    @property
    def profile_dir(self) -> Path:
        return BASE_PROFILE_DIR / self.account_id

    async def login(self, password: str) -> dict:
        if runtime_state.is_mock_mode():
            await asyncio.sleep(0.3)
            self.is_logged_in = True
            self.last_error = None
            return {"ok": True, "mock": True}
        try:
            return await asyncio.wait_for(self._do_real_login(password), timeout=45.0)
        except asyncio.TimeoutError:
            logger.warning("GPay login timed out for %s", self.email)
            self.last_error = "Login timed out (>45s). Google may be blocking automation — try again or complete a manual login once in the container."
            await self.close()
            return {"ok": False, "message": self.last_error, "timeout": True}

    async def _do_real_login(self, password: str) -> dict:
        try:
            from playwright.async_api import async_playwright
            self._playwright = await async_playwright().start()
            self.profile_dir.mkdir(parents=True, exist_ok=True)
            self._browser = await self._playwright.chromium.launch_persistent_context(
                str(self.profile_dir),
                headless=True,
                args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
            )
            self._page = self._browser.pages[0] if self._browser.pages else await self._browser.new_page()
            await self._page.goto(GPAY_URL, wait_until="domcontentloaded", timeout=25000)

            if "signin" not in self._page.url and "accounts.google" not in self._page.url:
                self.is_logged_in = True
                return {"ok": True, "message": "Restored session"}

            await self._page.fill('input[type="email"]', self.email)
            await self._page.click("#identifierNext")
            await self._page.wait_for_selector('input[type="password"]', timeout=8000)
            await self._page.fill('input[type="password"]', password)
            await self._page.click("#passwordNext")
            await self._page.wait_for_timeout(3000)

            if "challenge" in self._page.url:
                self.last_error = "2FA required — approve on your phone, then click Re-login."
                return {"ok": False, "requires_2fa": True, "message": self.last_error}

            self.is_logged_in = True
            return {"ok": True}
        except Exception as e:
            logger.exception("GPay login failed for %s", self.email)
            self.last_error = str(e)
            return {"ok": False, "message": str(e)}

    async def verify_utr(self, utr: str, amount: Optional[str] = None) -> dict:
        """Return { found: bool, ... }."""
        if runtime_state.is_mock_mode():
            await asyncio.sleep(0.2)
            found = bool(utr) and int(utr[-1]) % 2 == 0
            return {"found": found, "utr": utr, "amount": amount, "raw": "mock"}

        if not self.is_logged_in or not self._page:
            return {"found": False, "error": "Not logged in"}

        try:
            page = self._page
            await page.goto(f"{GPAY_URL}/transactions", wait_until="domcontentloaded", timeout=20000)
            search = await page.query_selector('input[type="search"], input[aria-label*="Search"]')
            if search:
                await search.fill(utr)
                await page.wait_for_timeout(2500)
            body = await page.inner_text("body")
            if utr in body or utr[-4:] in body:
                return {"found": True, "utr": utr}
            return {"found": False, "utr": utr}
        except Exception as e:
            logger.exception("GPay verify failed")
            return {"found": False, "error": str(e)}

    async def close(self):
        try:
            if self._browser: await self._browser.close()
            if self._playwright: await self._playwright.stop()
        except Exception:
            pass
        self.is_logged_in = False
        self._page = None
        self._browser = None


class GPayPoolService:
    """Manages multiple accounts. Verifies UTR across all active ones in parallel."""

    def __init__(self):
        self._sessions: dict[str, _AccountSession] = {}
        self._db = None

    def bind_db(self, db):
        self._db = db

    async def restore_from_db(self):
        """On startup, re-login every previously-active account using its stored (encrypted) password."""
        if self._db is None: return
        f = _fernet()
        async for doc in self._db.gpay_accounts.find({"is_active": True}):
            if not f or not doc.get("password_enc"):
                continue
            try:
                pw = f.decrypt(doc["password_enc"].encode()).decode()
            except InvalidToken:
                logger.warning("Could not decrypt password for %s", doc.get("email"))
                continue
            sess = _AccountSession(doc["id"], doc["email"])
            self._sessions[doc["id"]] = sess
            asyncio.create_task(self._async_login(doc["id"], pw))

    async def _async_login(self, account_id: str, password: str):
        try:
            sess = self._sessions[account_id]
            res = await sess.login(password)
            await self._db.gpay_accounts.update_one(
                {"id": account_id},
                {"$set": {"is_logged_in": bool(res.get("ok")),
                          "last_error": None if res.get("ok") else res.get("message"),
                          "last_login_at": _now()}},
            )
        except Exception:
            logger.exception("async login failed")

    # ---------- CRUD ----------
    async def add_account(self, label: str, email: str, password: str) -> dict:
        f = _fernet()
        if not f:
            raise RuntimeError("GPAY_ENC_KEY not configured")
        account_id = str(uuid.uuid4())
        doc = {
            "id": account_id,
            "label": label or email,
            "email": email,
            "password_enc": f.encrypt(password.encode()).decode(),
            "is_active": True,
            "is_logged_in": False,
            "hit_limit_at": None,
            "last_error": None,
            "added_at": _now(),
        }
        await self._db.gpay_accounts.insert_one(doc)
        sess = _AccountSession(account_id, email)
        self._sessions[account_id] = sess
        login_result = await sess.login(password)
        await self._db.gpay_accounts.update_one(
            {"id": account_id},
            {"$set": {"is_logged_in": bool(login_result.get("ok")),
                      "last_error": None if login_result.get("ok") else login_result.get("message"),
                      "last_login_at": _now()}},
        )
        fresh = await self._db.gpay_accounts.find_one({"id": account_id})
        return {"account": _safe_account(fresh), "login": login_result}

    async def list_accounts(self) -> list:
        docs = await self._db.gpay_accounts.find({}, {"_id": 0, "password_enc": 0}).sort("added_at", 1).to_list(50)
        return docs

    async def set_active(self, account_id: str, is_active: bool) -> dict:
        update: dict = {"is_active": is_active}
        if not is_active:
            update["hit_limit_at"] = _now()
        else:
            update["hit_limit_at"] = None
        r = await self._db.gpay_accounts.update_one({"id": account_id}, {"$set": update})
        if r.matched_count == 0:
            raise KeyError("account not found")
        # Also refresh session state
        if not is_active and account_id in self._sessions:
            await self._sessions[account_id].close()
            self._sessions.pop(account_id, None)
            await self._db.gpay_accounts.update_one({"id": account_id}, {"$set": {"is_logged_in": False}})
        if is_active and account_id not in self._sessions:
            f = _fernet()
            doc = await self._db.gpay_accounts.find_one({"id": account_id})
            if f and doc and doc.get("password_enc"):
                try:
                    pw = f.decrypt(doc["password_enc"].encode()).decode()
                    sess = _AccountSession(account_id, doc["email"])
                    self._sessions[account_id] = sess
                    asyncio.create_task(self._async_login(account_id, pw))
                except InvalidToken:
                    logger.warning("Cannot decrypt password to reactivate")
        return {"ok": True}

    async def delete_account(self, account_id: str) -> dict:
        sess = self._sessions.pop(account_id, None)
        if sess:
            await sess.close()
        r = await self._db.gpay_accounts.delete_one({"id": account_id})
        return {"ok": r.deleted_count > 0}

    async def re_login(self, account_id: str, password: Optional[str] = None) -> dict:
        doc = await self._db.gpay_accounts.find_one({"id": account_id})
        if not doc:
            raise KeyError("account not found")
        if password:
            f = _fernet()
            if not f:
                raise RuntimeError("GPAY_ENC_KEY not configured")
            await self._db.gpay_accounts.update_one(
                {"id": account_id}, {"$set": {"password_enc": f.encrypt(password.encode()).decode()}}
            )
        else:
            f = _fernet()
            if not f or not doc.get("password_enc"):
                return {"ok": False, "message": "No stored password; provide one"}
            try:
                password = f.decrypt(doc["password_enc"].encode()).decode()
            except InvalidToken:
                return {"ok": False, "message": "Stored password not decryptable"}
        sess = self._sessions.get(account_id) or _AccountSession(account_id, doc["email"])
        self._sessions[account_id] = sess
        await sess.close()
        result = await sess.login(password)
        await self._db.gpay_accounts.update_one(
            {"id": account_id},
            {"$set": {"is_logged_in": bool(result.get("ok")),
                      "last_error": None if result.get("ok") else result.get("message"),
                      "last_login_at": _now()}},
        )
        return result

    # ---------- Verify (parallel fan-out) ----------
    async def verify_utr(self, utr: str, amount: Optional[str] = None) -> dict:
        """Verify UTR across every ACTIVE + LOGGED-IN account, in parallel.
        Return the first successful hit or a not-found result if all fail."""
        active_docs = await self._db.gpay_accounts.find(
            {"is_active": True}, {"_id": 0, "password_enc": 0}
        ).to_list(20)

        checkable = [a for a in active_docs if a["id"] in self._sessions]
        if not checkable:
            return {"found": False, "utr": utr, "reason": "no active accounts online",
                    "checked_accounts": []}

        async def _check(acct):
            sess = self._sessions[acct["id"]]
            res = await sess.verify_utr(utr, amount)
            return {"account_id": acct["id"], "label": acct["label"], **res}

        results = await asyncio.gather(*[_check(a) for a in checkable], return_exceptions=True)
        clean: list[dict] = []
        for r in results:
            if isinstance(r, Exception):
                clean.append({"error": str(r)})
            else:
                clean.append(r)

        winner = next((r for r in clean if r.get("found")), None)
        if winner:
            return {
                "found": True, "utr": utr, "amount": amount,
                "account_id": winner["account_id"],
                "account_label": winner["label"],
                "checked_accounts": [c.get("label") for c in clean],
            }
        return {
            "found": False, "utr": utr, "amount": amount,
            "checked_accounts": [c.get("label") for c in clean],
        }

    async def close_all(self):
        for sess in list(self._sessions.values()):
            await sess.close()
        self._sessions.clear()


gpay_pool = GPayPoolService()

# ---- Backwards-compatible shim so old imports still work ----
class _LegacyShim:
    @property
    def is_logged_in(self) -> bool:
        return any(s.is_logged_in for s in gpay_pool._sessions.values())

    @property
    def email(self) -> Optional[str]:
        for s in gpay_pool._sessions.values():
            if s.is_logged_in:
                return s.email
        return None

    async def verify_utr(self, utr: str, amount: Optional[str] = None) -> dict:
        return await gpay_pool.verify_utr(utr, amount)

    async def close(self):
        await gpay_pool.close_all()

    async def login(self, email: str, password: str) -> dict:
        """Legacy /api/gpay/login: adds account labelled "Default" (or updates it)."""
        # If an account with this email exists, reuse it, else create.
        existing = await gpay_pool._db.gpay_accounts.find_one({"email": email})
        if existing:
            return await gpay_pool.re_login(existing["id"], password=password)
        r = await gpay_pool.add_account(label="Default", email=email, password=password)
        return r["login"]


gpay_service = _LegacyShim()
