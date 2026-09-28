"""WhatsApp Web automation via Playwright.

- Logs in via QR code (persistent profile so scan happens once).
- Watches a configured group for image messages.
- For each new image message, downloads it and hands off to the bot orchestrator.
- Sends reply as a quoted reply to the payment message.

Set BOT_MOCK_MODE=true to simulate.
"""
import os
import asyncio
import logging
from pathlib import Path
from typing import Optional, Callable, Awaitable
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")
logger = logging.getLogger(__name__)

MOCK_MODE = os.environ.get("BOT_MOCK_MODE", "false").lower() == "true"
WA_URL = "https://web.whatsapp.com/"
USER_DATA_DIR = Path("/tmp/whatsapp_profile")


class WhatsAppService:
    def __init__(self):
        self._playwright = None
        self._browser = None
        self._page = None
        self._connected = False
        self._qr_data_url: Optional[str] = None
        self._group_name: Optional[str] = None
        self._monitor_task: Optional[asyncio.Task] = None
        self._on_image_callback: Optional[Callable[[str, bytes, str], Awaitable[None]]] = None

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def qr_data_url(self) -> Optional[str]:
        return self._qr_data_url

    async def start(self, group_name: str, on_image):
        self._group_name = group_name
        self._on_image_callback = on_image
        if MOCK_MODE:
            self._connected = True
            self._qr_data_url = None
            logger.info("WhatsApp service started (mock mode) for group %s", group_name)
            return {"ok": True, "mock": True}

        try:
            from playwright.async_api import async_playwright
            self._playwright = await async_playwright().start()
            USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
            self._browser = await self._playwright.chromium.launch_persistent_context(
                str(USER_DATA_DIR),
                headless=True,
                args=["--no-sandbox"],
            )
            self._page = self._browser.pages[0] if self._browser.pages else await self._browser.new_page()
            await self._page.goto(WA_URL, wait_until="domcontentloaded", timeout=60000)
            # Grab QR canvas if present
            qr = await self._page.query_selector('canvas[aria-label*="Scan"]')
            if qr:
                self._qr_data_url = "data:image/png;base64," + (await qr.screenshot()).hex()
            # Wait for chat list
            try:
                await self._page.wait_for_selector('[data-testid="chat-list"]', timeout=120000)
                self._connected = True
            except Exception:
                self._connected = False

            self._monitor_task = asyncio.create_task(self._monitor_loop())
            return {"ok": self._connected}
        except Exception as e:
            logger.exception("WhatsApp start failed")
            return {"ok": False, "error": str(e)}

    async def _monitor_loop(self):
        # This is a stub — a real implementation needs to open the group,
        # observe DOM changes for new image messages, download each, and call
        # self._on_image_callback(sender, image_bytes, message_id).
        while True:
            await asyncio.sleep(5)

    async def send_reply(self, message_id: str, text: str) -> bool:
        if MOCK_MODE:
            logger.info("[MOCK WA reply] msg=%s -> %s", message_id, text)
            return True
        # Real implementation: locate msg by id, click reply, type & send.
        return False

    async def stop(self):
        if self._monitor_task:
            self._monitor_task.cancel()
        try:
            if self._browser:
                await self._browser.close()
            if self._playwright:
                await self._playwright.stop()
        except Exception:
            pass
        self._connected = False


whatsapp_service = WhatsAppService()
