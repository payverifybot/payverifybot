"""WhatsApp Web automation via Playwright.

Real DOM watcher for a target group:
  - persistent chromium profile (survives QR scan across restarts)
  - opens web.whatsapp.com, locates the group by name
  - polls the message pane for NEW image messages (unseen ids)
  - downloads each image via a canvas.toBlob trick (survives WA's blob URLs)
  - calls on_image(sender, image_bytes, message_id) for each new one
  - send_reply() clicks reply-to on a message and posts text

Set BOT_MOCK_MODE=true to simulate (no browser).
"""
import os
import re
import base64
import asyncio
import logging
from pathlib import Path
from typing import Optional, Callable, Awaitable
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")
logger = logging.getLogger(__name__)

MOCK_MODE = os.environ.get("BOT_MOCK_MODE", "false").lower() == "true"
WA_URL = "https://web.whatsapp.com/"
USER_DATA_DIR = Path(os.environ.get("WA_PROFILE_DIR", "/tmp/whatsapp_profile"))
POLL_INTERVAL = float(os.environ.get("WA_POLL_INTERVAL", "3"))


# JS run in-page to enumerate image messages currently rendered in the open chat.
# Returns a list of { id, sender, dataUrl }. WA's messages have a
# data-id attribute like "true_120363@g.us_ABCDEF..." and image bubbles contain
# a rendered <img> whose src is a blob: URL. We paint the <img> onto a canvas
# and export as a data URL so Python can pull the bytes back with one call.
_JS_EXTRACT_IMAGES = r"""
async () => {
  const rows = document.querySelectorAll('div[role="row"], div._amjy, div.message-in, div.message-out');
  const out = [];
  for (const row of rows) {
    const idEl = row.querySelector('[data-id]');
    const id = idEl ? idEl.getAttribute('data-id') : null;
    if (!id) continue;
    const img = row.querySelector('img[src^="blob:"]');
    if (!img) continue;
    // sender: for group chats, WA renders a colored span above the bubble
    const senderEl = row.querySelector('span._ao3e, [aria-label]');
    const sender = senderEl ? (senderEl.getAttribute('aria-label') || senderEl.textContent || '').trim() : '';
    try {
      await img.decode();
      const c = document.createElement('canvas');
      c.width = img.naturalWidth; c.height = img.naturalHeight;
      c.getContext('2d').drawImage(img, 0, 0);
      out.push({ id, sender, dataUrl: c.toDataURL('image/jpeg', 0.92) });
    } catch (e) { /* skip */ }
  }
  return out;
}
"""

_JS_OPEN_GROUP = r"""
async (name) => {
  // click the search box
  const searchBtn = document.querySelector('button[aria-label="Search or start new chat"], div[aria-label="Search input textbox"]');
  if (searchBtn) searchBtn.click();
  await new Promise(r => setTimeout(r, 300));
  const search = document.querySelector('div[contenteditable="true"][data-tab="3"], div[contenteditable="true"][role="textbox"]');
  if (!search) return { ok: false, reason: "no-search-box" };
  search.focus();
  document.execCommand('selectAll', false, null);
  document.execCommand('insertText', false, name);
  await new Promise(r => setTimeout(r, 600));
  const rows = document.querySelectorAll('div[role="listitem"], div._ak8l');
  for (const r of rows) {
    if ((r.textContent || '').includes(name)) { r.click(); return { ok: true }; }
  }
  return { ok: false, reason: "not-found" };
}
"""


class WhatsAppService:
    def __init__(self):
        self._playwright = None
        self._browser = None
        self._page = None
        self._connected = False
        self._qr_data_url: Optional[str] = None
        self._group_names: list[str] = []
        self._monitor_task: Optional[asyncio.Task] = None
        self._on_image_callback = None
        self._seen_by_group: dict[str, set[str]] = {}
        self._last_error: Optional[str] = None
        self._active_group: Optional[str] = None

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def qr_data_url(self) -> Optional[str]:
        return self._qr_data_url

    @property
    def last_error(self) -> Optional[str]:
        return self._last_error

    @property
    def active_group(self) -> Optional[str]:
        return self._active_group

    @property
    def group_names(self) -> list[str]:
        return list(self._group_names)

    async def start(self, group_names, on_image):
        # Accept either a single string (backwards compat) or a list
        if isinstance(group_names, str):
            group_names = [g.strip() for g in group_names.split(",") if g.strip()]
        self._group_names = [g for g in (group_names or []) if g]
        self._on_image_callback = on_image
        self._last_error = None
        self._seen_by_group = {g: set() for g in self._group_names}

        if MOCK_MODE:
            self._connected = True
            self._qr_data_url = None
            logger.info("WhatsApp (mock) started for groups %s", self._group_names)
            return {"ok": True, "mock": True, "groups": self._group_names}

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
            await self._page.goto(WA_URL, wait_until="domcontentloaded", timeout=60000)

            try:
                await self._page.wait_for_selector(
                    'canvas[aria-label*="Scan"], [data-testid="chat-list"], [aria-label="Chat list"]',
                    timeout=120000,
                )
            except Exception:
                pass

            qr = await self._page.query_selector('canvas[aria-label*="Scan"]')
            if qr:
                png = await qr.screenshot(type="png")
                self._qr_data_url = "data:image/png;base64," + base64.b64encode(png).decode()
                try:
                    await self._page.wait_for_selector('[data-testid="chat-list"], [aria-label="Chat list"]', timeout=180000)
                    self._qr_data_url = None
                except Exception:
                    self._last_error = "QR not scanned in time"
                    self._connected = False
                    return {"ok": False, "error": self._last_error}

            self._connected = True
            self._monitor_task = asyncio.create_task(self._monitor_loop())
            return {"ok": True, "groups": self._group_names}
        except Exception as e:
            logger.exception("WhatsApp start failed")
            self._last_error = str(e)
            return {"ok": False, "error": str(e)}

    async def _monitor_loop(self):
        """Rotate through configured groups. On first visit to a group we PRIME
        seen_ids (so history isn't reprocessed); on subsequent visits we emit
        only images with a new data-id."""
        idx = 0
        while True:
            try:
                await asyncio.sleep(POLL_INTERVAL)
                if not self._page or not self._connected or not self._group_names:
                    continue
                group = self._group_names[idx % len(self._group_names)]
                idx += 1
                self._active_group = group

                # Switch to this chat
                open_res = await self._page.evaluate(_JS_OPEN_GROUP, group)
                if not open_res.get("ok"):
                    logger.warning("Could not open group %s: %s", group, open_res.get("reason"))
                    continue
                await asyncio.sleep(1.2)

                messages = await self._page.evaluate(_JS_EXTRACT_IMAGES)
                seen = self._seen_by_group.setdefault(group, set())
                first_visit = not seen
                if first_visit:
                    # Prime — don't re-process history
                    seen.update(m["id"] for m in messages if m.get("id"))
                    continue

                for m in messages:
                    mid = m.get("id")
                    if not mid or mid in seen:
                        continue
                    seen.add(mid)
                    data_url = m.get("dataUrl") or ""
                    if "," not in data_url:
                        continue
                    try:
                        image_bytes = base64.b64decode(data_url.split(",", 1)[1])
                    except Exception:
                        continue
                    sender = m.get("sender") or "unknown"
                    logger.info("[%s] New image from %s (msg=%s, %d bytes)",
                                group, sender, mid, len(image_bytes))
                    if self._on_image_callback:
                        try:
                            await self._on_image_callback(sender, image_bytes, mid, group)
                        except Exception:
                            logger.exception("on_image callback failed")
            except asyncio.CancelledError:
                return
            except Exception:
                logger.exception("WA monitor loop error")

    async def send_reply(self, message_id: str, text: str) -> bool:
        if MOCK_MODE:
            logger.info("[MOCK WA reply] msg=%s -> %s", message_id, text)
            return True
        if not self._page:
            return False
        try:
            # Find the message row, hover, click the menu, click Reply, type, send
            js = r"""
            async ([msgId, text]) => {
              const row = document.querySelector(`[data-id="${msgId}"]`)?.closest('div[role="row"], div._amjy');
              if (!row) return { ok: false, reason: 'row-not-found' };
              row.scrollIntoView({block: 'center'});
              const evt = (type) => row.dispatchEvent(new MouseEvent(type, {bubbles:true}));
              evt('mouseover'); evt('mousemove');
              await new Promise(r => setTimeout(r, 200));
              const menuBtn = row.querySelector('[aria-label="Context menu"], [data-icon="down-context"]');
              if (!menuBtn) return { ok: false, reason: 'no-menu' };
              menuBtn.click();
              await new Promise(r => setTimeout(r, 250));
              const items = document.querySelectorAll('[role="menuitem"], li');
              let clicked = false;
              for (const it of items) {
                if ((it.textContent || '').trim().toLowerCase().startsWith('reply')) { it.click(); clicked = true; break; }
              }
              if (!clicked) return { ok: false, reason: 'no-reply-item' };
              await new Promise(r => setTimeout(r, 250));
              const box = document.querySelector('footer div[contenteditable="true"][role="textbox"]');
              if (!box) return { ok: false, reason: 'no-input' };
              box.focus();
              document.execCommand('insertText', false, text);
              await new Promise(r => setTimeout(r, 200));
              const sendBtn = document.querySelector('button[aria-label="Send"], [data-icon="send"]');
              if (sendBtn) sendBtn.click();
              else box.dispatchEvent(new KeyboardEvent('keydown', {key:'Enter', bubbles:true}));
              return { ok: true };
            }
            """
            res = await self._page.evaluate(js, [message_id, text])
            return bool(res and res.get("ok"))
        except Exception:
            logger.exception("send_reply failed")
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
        self._page = None
        self._browser = None
        self._qr_data_url = None


whatsapp_service = WhatsAppService()
