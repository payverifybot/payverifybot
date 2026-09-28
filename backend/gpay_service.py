"""GPay Business dashboard automation.

Uses Playwright to log into business.google.com/payments (GPay for Business)
and search for a transaction by UTR.

IMPORTANT: Google login has strong bot detection. In the container this runs
headless. For production use, run this bot locally with a persistent user-data
dir so cookies survive across restarts.

Set BOT_MOCK_MODE=true in .env to simulate results without launching a browser.
"""
import os
import asyncio
import logging
from typing import Optional
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

logger = logging.getLogger(__name__)

MOCK_MODE = os.environ.get("BOT_MOCK_MODE", "false").lower() == "true"
GPAY_URL = "https://business.google.com/payments"
USER_DATA_DIR = Path("/tmp/gpay_profile")


class GPayService:
    def __init__(self):
        self._playwright = None
        self._browser = None
        self._page = None
        self._logged_in = False
        self._email: Optional[str] = None

    @property
    def is_logged_in(self) -> bool:
        return self._logged_in

    @property
    def email(self) -> Optional[str]:
        return self._email

    async def login(self, email: str, password: str) -> dict:
        """Attempt to log in. In real mode this WILL usually trip Google's bot
        protection; the returned dict tells the caller if OTP/2FA is required.
        """
        self._email = email
        if MOCK_MODE:
            await asyncio.sleep(1)
            self._logged_in = True
            return {"ok": True, "mock": True, "message": "Logged in (mock mode)"}

        try:
            from playwright.async_api import async_playwright
            self._playwright = await async_playwright().start()
            USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
            self._browser = await self._playwright.chromium.launch_persistent_context(
                str(USER_DATA_DIR),
                headless=True,
                args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
            )
            self._page = self._browser.pages[0] if self._browser.pages else await self._browser.new_page()
            await self._page.goto(GPAY_URL, wait_until="domcontentloaded", timeout=30000)

            # If already logged in via persistent cookies, we're done
            if "signin" not in self._page.url and "accounts.google" not in self._page.url:
                self._logged_in = True
                return {"ok": True, "message": "Restored session from cookies"}

            # Enter email
            await self._page.fill('input[type="email"]', email)
            await self._page.click('#identifierNext')
            await self._page.wait_for_selector('input[type="password"]', timeout=10000)
            await self._page.fill('input[type="password"]', password)
            await self._page.click('#passwordNext')
            await self._page.wait_for_timeout(4000)

            if "challenge" in self._page.url or "signin/v2/challenge" in self._page.url:
                return {
                    "ok": False,
                    "requires_2fa": True,
                    "message": "Google requires 2FA — complete it in the browser session, then click 'Refresh Session'.",
                }

            self._logged_in = True
            return {"ok": True, "message": "Logged in"}
        except Exception as e:
            logger.exception("GPay login failed")
            return {"ok": False, "message": f"Login failed: {e}"}

    async def verify_utr(self, utr: str, amount: Optional[str] = None) -> dict:
        """Return { found: bool, amount, payer, timestamp, raw }."""
        if MOCK_MODE:
            await asyncio.sleep(0.8)
            # Simulate: last digit even -> found, odd -> not found (deterministic demo)
            found = bool(utr) and int(utr[-1]) % 2 == 0
            return {
                "found": found,
                "utr": utr,
                "amount": amount or ("1500.00" if found else None),
                "payer": "Ramesh Kumar" if found else None,
                "timestamp": "2026-01-15 14:32" if found else None,
                "raw": "mock",
            }

        if not self._logged_in or not self._page:
            return {"found": False, "error": "Not logged in to GPay Business"}

        try:
            page = self._page
            # Navigate to transactions page (URL varies; this is a best-effort selector)
            await page.goto(f"{GPAY_URL}/transactions", wait_until="domcontentloaded", timeout=20000)
            # Search by UTR
            search = await page.query_selector('input[type="search"], input[aria-label*="Search"]')
            if search:
                await search.fill(utr)
                await page.wait_for_timeout(2500)
            body_text = await page.inner_text("body")
            if utr in body_text:
                return {"found": True, "utr": utr, "raw": "matched-on-page"}
            # Also try last-4 match
            if utr[-4:] in body_text:
                return {"found": True, "utr": utr, "raw": "last4-matched"}
            return {"found": False, "utr": utr, "raw": "not-in-page"}
        except Exception as e:
            logger.exception("GPay verify failed")
            return {"found": False, "error": str(e)}

    async def close(self):
        try:
            if self._browser:
                await self._browser.close()
            if self._playwright:
                await self._playwright.stop()
        except Exception:
            pass
        self._logged_in = False
        self._page = None
        self._browser = None


gpay_service = GPayService()
