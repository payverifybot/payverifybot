"""GPay Business — multi-account pool with rich, human-in-the-loop login.

Each account has its own Playwright session against
https://business.google.com/payments. Verification checks the UTR across
every currently-online account in parallel; the first hit wins.

Login flow now surfaces a per-account "step":

    idle -> launching -> loading_login -> entering_email -> entering_password
         -> awaiting_2fa    (operator approves the prompt on their phone)
         -> awaiting_captcha (operator solves the puzzle in the headed browser)
         -> awaiting_review  (any other interstitial — e.g. "This browser is
                              not supported"; operator resolves manually)
         -> opening_dashboard -> logged_in
         -> error

While in an "awaiting_*" state the background task keeps polling the page
until it detects the operator has resolved the interstitial, THEN
automatically continues opening the transactions dashboard. The bot
never gives up — it waits up to 10 minutes on any manual step, and
takes a fresh screenshot every ~3s so the operator can see progress.
"""
import os
import uuid
import asyncio
import base64
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
# The transactions view we search for UTRs in. If Google rearranges this path,
# override via env var (e.g. https://pay.google.com/business/console/#transactions).
GPAY_TXN_URL = os.environ.get("GPAY_TXN_URL", "https://business.google.com/payments/transactions")
BASE_PROFILE_DIR = Path(os.environ.get("GPAY_PROFILE_DIR", "/tmp/gpay_profiles"))
ENC_KEY = os.environ.get("GPAY_ENC_KEY")

MANUAL_STEP_TIMEOUT = float(os.environ.get("GPAY_MANUAL_TIMEOUT_SECS", "600"))  # 10 min per manual step
SNAP_INTERVAL = float(os.environ.get("GPAY_SNAP_INTERVAL_SECS", "3"))


def _headless() -> bool:
    return os.environ.get("PLAYWRIGHT_HEADLESS", "true").lower() != "false"


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
    return {k: v for k, v in doc.items() if k not in ("password_enc", "_id")}


class _AccountSession:
    """Playwright session for ONE GPay Business account."""

    def __init__(self, account_id: str, email: str):
        self.account_id = account_id
        self.email = email
        self._playwright = None
        self._browser = None
        self._page = None
        self._login_task: Optional[asyncio.Task] = None

        # observable state
        self.is_logged_in: bool = False
        self.last_error: Optional[str] = None
        self.step: str = "idle"
        self.prompt: Optional[str] = None
        self.current_url: Optional[str] = None
        self.screenshot_b64: Optional[str] = None
        self.updated_at: str = _now()

    @property
    def profile_dir(self) -> Path:
        return BASE_PROFILE_DIR / self.account_id

    def status(self) -> dict:
        return {
            "step": self.step,
            "is_logged_in": self.is_logged_in,
            "prompt": self.prompt,
            "last_error": self.last_error,
            "current_url": self.current_url,
            "screenshot_b64": self.screenshot_b64,
            "updated_at": self.updated_at,
        }

    def _set(self, *, step: Optional[str] = None, prompt: Optional[str] = None,
             error: Optional[str] = None):
        if step is not None:
            self.step = step
        if prompt is not None:
            self.prompt = prompt or None
        if error is not None:
            self.last_error = error or None
        self.updated_at = _now()
        logger.info("[GPay %s] step=%s prompt=%r err=%r",
                    self.email, self.step, self.prompt, self.last_error)

    async def _snap(self):
        if not self._page:
            return
        try:
            png = await self._page.screenshot(type="png", full_page=False)
            self.screenshot_b64 = base64.b64encode(png).decode()
            self.current_url = self._page.url
            self.updated_at = _now()
        except Exception:
            pass

    # ---------- login ----------
    async def login(self, password: str) -> dict:
        # cancel prior background login
        if self._login_task and not self._login_task.done():
            self._login_task.cancel()

        if runtime_state.is_mock_mode():
            await asyncio.sleep(0.2)
            self.is_logged_in = True
            self._set(step="logged_in", prompt=None, error=None)
            return {"ok": True, "mock": True, "step": self.step}

        # Kick off login in the background; return quickly so UI can poll.
        self._set(step="launching", prompt="Starting Chromium and opening Google sign-in…", error=None)
        self._login_task = asyncio.create_task(self._do_login(password))
        return {
            "ok": True,
            "background": True,
            "step": self.step,
            "message": "Login started. Watch the live status card — approve any 2-step prompt on your phone when it appears.",
        }

    async def _do_login(self, password: str):
        try:
            from playwright.async_api import async_playwright
            self._playwright = await async_playwright().start()
            self.profile_dir.mkdir(parents=True, exist_ok=True)
            self._browser = await self._playwright.chromium.launch_persistent_context(
                str(self.profile_dir),
                headless=_headless(),
                args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
                viewport={"width": 1280, "height": 800},
            )
            self._page = self._browser.pages[0] if self._browser.pages else await self._browser.new_page()

            self._set(step="loading_login", prompt="Opening business.google.com/payments…")
            await self._page.goto(GPAY_URL, wait_until="domcontentloaded", timeout=45000)
            await self._snap()

            # If we've already got a live session in this profile, we may skip the whole login.
            if await self._looks_logged_in():
                await self._finalize_dashboard()
                return

            # 1. email
            try:
                await self._page.wait_for_selector('input[type="email"]', timeout=15000)
                self._set(step="entering_email", prompt="Typing email…")
                await self._page.fill('input[type="email"]', self.email)
                await self._snap()
                await self._page.click("#identifierNext, button:has-text('Next')")
            except Exception as e:
                # Maybe we skipped straight to a dashboard/interstitial
                if await self._looks_logged_in():
                    await self._finalize_dashboard()
                    return
                raise RuntimeError(f"Could not find the email field. Google may be showing an unexpected page. ({e})")

            # 2. password
            try:
                await self._page.wait_for_selector('input[type="password"]', timeout=15000)
                self._set(step="entering_password", prompt="Typing password…")
                await self._page.fill('input[type="password"]', password)
                await self._snap()
                await self._page.click("#passwordNext, button:has-text('Next')")
            except Exception as e:
                # Some accounts jump straight from email -> 2FA (passkey / prompt)
                if not await self._page.query_selector('input[type="password"]'):
                    logger.info("No password field — probably passkey/phone-approval only.")
                else:
                    raise RuntimeError(f"Password step failed: {e}")

            await self._page.wait_for_timeout(1500)
            await self._snap()

            # 3. wait for one of: dashboard | 2FA | captcha | error message
            ok = await self._wait_for_login_outcome()
            if not ok:
                return  # already set to error state

            await self._finalize_dashboard()

        except asyncio.CancelledError:
            return
        except Exception as e:
            logger.exception("GPay login failed for %s", self.email)
            self._set(step="error", error=f"{type(e).__name__}: {e}", prompt=None)
            try:
                await self._snap()
            except Exception:
                pass

    async def _wait_for_login_outcome(self) -> bool:
        """Poll the page until we either succeed, or spot a manual step to resolve.
        Returns True if we can proceed to the dashboard, False otherwise."""
        deadline = asyncio.get_event_loop().time() + MANUAL_STEP_TIMEOUT
        last_prompt_step: Optional[str] = None
        while asyncio.get_event_loop().time() < deadline:
            if not self._page:
                return False
            try:
                url = self._page.url
                body_text = ""
                try:
                    body_text = (await self._page.inner_text("body"))[:4000].lower()
                except Exception:
                    pass

                # ---- success? -----------------------------------------------
                if await self._looks_logged_in():
                    return True

                # ---- password wrong? ----------------------------------------
                if "wrong password" in body_text or "couldn’t find your google account" in body_text \
                        or "couldn't find your google account" in body_text:
                    self._set(step="error",
                              error="Google says the email or password is wrong. Fix it and hit Re-login.",
                              prompt=None)
                    await self._snap()
                    return False

                # ---- 2FA prompts --------------------------------------------
                is_2fa = (
                    "challenge/" in url
                    or "signin/v2/challenge" in url
                    or "check your" in body_text and "phone" in body_text
                    or "2-step verification" in body_text
                    or "verify it" in body_text and "you" in body_text
                    or "tap yes" in body_text
                    or "get a verification code" in body_text
                )
                if is_2fa:
                    if last_prompt_step != "awaiting_2fa":
                        self._set(step="awaiting_2fa",
                                  prompt="Google is asking for 2-step verification. Approve the prompt on your phone (or enter the code in the browser window). The bot will continue automatically when done.",
                                  error=None)
                        last_prompt_step = "awaiting_2fa"
                    await self._snap()
                    await asyncio.sleep(SNAP_INTERVAL)
                    continue

                # ---- captcha ------------------------------------------------
                has_captcha = await self._page.query_selector('img#captchaimg, iframe[src*="recaptcha"], div.g-recaptcha')
                if has_captcha or "unusual traffic" in body_text or "prove you" in body_text:
                    if last_prompt_step != "awaiting_captcha":
                        self._set(step="awaiting_captcha",
                                  prompt="Google is asking for a captcha. Solve it in the browser window that just opened on your desktop — the bot will continue automatically once you pass it.",
                                  error=None)
                        last_prompt_step = "awaiting_captcha"
                    await self._snap()
                    await asyncio.sleep(SNAP_INTERVAL)
                    continue

                # ---- unsupported / other interstitial -----------------------
                if "browser is not supported" in body_text or "couldn't sign you in" in body_text \
                        or "sign in isn't secure" in body_text:
                    if last_prompt_step != "awaiting_review":
                        self._set(step="awaiting_review",
                                  prompt="Google is showing an interstitial (unsupported browser / security block). Resolve it in the browser window — the bot will detect the dashboard and continue automatically.",
                                  error=None)
                        last_prompt_step = "awaiting_review"
                    await self._snap()
                    await asyncio.sleep(SNAP_INTERVAL)
                    continue

                # ---- unknown state — keep watching --------------------------
                if last_prompt_step != "watching":
                    self._set(step="awaiting_review",
                              prompt="Waiting for Google's next screen… if you see anything to click or type, do it in the browser window; the bot will continue automatically.",
                              error=None)
                    last_prompt_step = "watching"
                await self._snap()
                await asyncio.sleep(SNAP_INTERVAL)
            except Exception:
                logger.exception("[GPay %s] wait-outcome tick failed", self.email)
                await asyncio.sleep(SNAP_INTERVAL)

        # timed out
        self._set(step="error",
                  error="Manual step did not complete within 10 minutes. Approve 2FA / captcha and click Re-login.",
                  prompt=None)
        await self._snap()
        return False

    async def _looks_logged_in(self) -> bool:
        """Heuristic: are we on the GPay Business console (post-login)?"""
        if not self._page:
            return False
        try:
            url = self._page.url
            if any(s in url for s in ["business.google.com/payments", "pay.google.com/business",
                                       "pay.google.com/gp/w"]):
                # And no login form on screen
                has_pw = await self._page.query_selector('input[type="password"]')
                has_email = await self._page.query_selector('input[type="email"]')
                if not has_pw and not has_email:
                    return True
            return False
        except Exception:
            return False

    async def _finalize_dashboard(self):
        """Navigate to the transactions view so verify_utr is fast later."""
        self._set(step="opening_dashboard", prompt="Opening the transactions dashboard…", error=None)
        try:
            await self._page.goto(GPAY_TXN_URL, wait_until="domcontentloaded", timeout=30000)
            await self._page.wait_for_timeout(1500)
            await self._snap()
        except Exception as e:
            logger.warning("Transactions page nav failed (non-fatal): %s", e)
        self.is_logged_in = True
        self._set(step="logged_in",
                  prompt="Logged in. The bot is now watching for UTRs and will verify them against this account.",
                  error=None)
        await self._snap()

    # ---------- verify ----------
    async def verify_utr(self, utr: str, amount: Optional[str] = None) -> dict:
        if runtime_state.is_mock_mode():
            await asyncio.sleep(0.15)
            found = bool(utr) and utr[-1].isdigit() and int(utr[-1]) % 2 == 0
            return {"found": found, "utr": utr, "amount": amount, "raw": "mock"}

        if not self.is_logged_in or not self._page:
            return {"found": False, "error": "Not logged in", "step": self.step}

        try:
            page = self._page
            await page.goto(GPAY_TXN_URL, wait_until="domcontentloaded", timeout=25000)
            search = await page.query_selector('input[type="search"], input[aria-label*="Search"]')
            if search:
                await search.fill(utr)
                await page.wait_for_timeout(2500)
            body = await page.inner_text("body")
            if utr in body or (len(utr) >= 4 and utr[-4:] in body):
                return {"found": True, "utr": utr}
            return {"found": False, "utr": utr}
        except Exception as e:
            logger.exception("GPay verify failed")
            return {"found": False, "error": str(e)}

    async def close(self):
        if self._login_task and not self._login_task.done():
            self._login_task.cancel()
        try:
            if self._browser:
                await self._browser.close()
            if self._playwright:
                await self._playwright.stop()
        except Exception:
            pass
        self.is_logged_in = False
        self._page = None
        self._browser = None
        self._playwright = None
        self._set(step="idle", prompt=None, error=None)


class GPayPoolService:
    """Manages multiple accounts. Verifies UTR across every online account in parallel."""

    def __init__(self):
        self._sessions: dict[str, _AccountSession] = {}
        self._db = None

    def bind_db(self, db):
        self._db = db

    async def restore_from_db(self):
        if self._db is None:
            return
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
            await sess.login(password)
            asyncio.create_task(self._track_login(account_id))
        except Exception:
            logger.exception("async login failed")

    async def _track_login(self, account_id: str):
        """Follow a session's login task and mirror the final state into Mongo."""
        sess = self._sessions.get(account_id)
        if not sess or self._db is None:
            return
        t = sess._login_task
        if t is None:
            return
        try:
            await t
        except Exception:
            pass
        await self._db.gpay_accounts.update_one(
            {"id": account_id},
            {"$set": {
                "is_logged_in": sess.is_logged_in,
                "last_error": sess.last_error,
                "last_step": sess.step,
                "last_login_at": _now(),
            }},
        )

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
            "last_step": "launching",
            "added_at": _now(),
        }
        await self._db.gpay_accounts.insert_one(doc)
        sess = _AccountSession(account_id, email)
        self._sessions[account_id] = sess
        login_result = await sess.login(password)
        # Mirror step so UI has something immediately
        await self._db.gpay_accounts.update_one(
            {"id": account_id},
            {"$set": {"last_step": sess.step, "last_error": sess.last_error}},
        )
        # Track final state async
        asyncio.create_task(self._track_login(account_id))
        fresh = await self._db.gpay_accounts.find_one({"id": account_id}, {"_id": 0, "password_enc": 0})
        return {"account": _safe_account(fresh), "login": login_result}

    async def list_accounts(self) -> list:
        docs = await self._db.gpay_accounts.find({}, {"_id": 0, "password_enc": 0}).sort("added_at", 1).to_list(50)
        # Enrich with live session state so the UI can react instantly
        for d in docs:
            sess = self._sessions.get(d["id"])
            if sess:
                d["is_logged_in"] = sess.is_logged_in
                d["last_step"] = sess.step
                d["last_error"] = sess.last_error
                d["current_url"] = sess.current_url
                d["prompt"] = sess.prompt
                d["updated_at"] = sess.updated_at
        return docs

    async def get_status(self, account_id: str) -> dict:
        sess = self._sessions.get(account_id)
        if not sess:
            # Try to lazily create one from DB (paused/re-loaded case)
            doc = await self._db.gpay_accounts.find_one({"id": account_id}, {"_id": 0})
            if not doc:
                raise KeyError("account not found")
            return {
                "step": doc.get("last_step") or "idle",
                "is_logged_in": bool(doc.get("is_logged_in")),
                "prompt": None,
                "last_error": doc.get("last_error"),
                "current_url": None,
                "screenshot_b64": None,
                "updated_at": doc.get("last_login_at") or doc.get("added_at"),
            }
        return sess.status()

    async def set_active(self, account_id: str, is_active: bool) -> dict:
        update: dict = {"is_active": is_active}
        if not is_active:
            update["hit_limit_at"] = _now()
        else:
            update["hit_limit_at"] = None
        r = await self._db.gpay_accounts.update_one({"id": account_id}, {"$set": update})
        if r.matched_count == 0:
            raise KeyError("account not found")
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
        asyncio.create_task(self._track_login(account_id))
        return result

    # ---------- Verify ----------
    async def verify_utr(self, utr: str, amount: Optional[str] = None) -> dict:
        active_docs = await self._db.gpay_accounts.find(
            {"is_active": True}, {"_id": 0, "password_enc": 0}
        ).to_list(20)

        checkable = [a for a in active_docs
                     if a["id"] in self._sessions and self._sessions[a["id"]].is_logged_in]
        if not checkable:
            return {"found": False, "utr": utr,
                    "reason": "no active accounts online",
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


# ---- Backwards-compatible shim ----
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
        existing = await gpay_pool._db.gpay_accounts.find_one({"email": email})
        if existing:
            return await gpay_pool.re_login(existing["id"], password=password)
        r = await gpay_pool.add_account(label="Default", email=email, password=password)
        return r["login"]


gpay_service = _LegacyShim()
