"""WhatsApp Web automation via Playwright — with rich live status.

Public state (surfaced by /api/whatsapp/status):
    step           one of: idle | launching | awaiting_qr | scanning | linking |
                            connected | error | stopped
    qr_data_url    data-URL of the CURRENT QR PNG (auto-refreshed every ~5s while
                   awaiting_qr). WhatsApp rotates its own QR every ~60s; we just
                   re-screenshot the on-page canvas so the UI always shows a
                   live, scannable code.
    screenshot_b64 latest full-page screenshot (base64 PNG) — updated on every
                   state change and every QR refresh, so the operator sees
                   exactly what the bot's browser sees.
    last_error     human-readable message on failure.
    prompt         short instruction to the operator when human action is
                   required (e.g. "Scan this QR from your phone").

Set the runtime mock flag (via /api/mode) to bypass Playwright entirely.
"""
import os
import base64
import asyncio
import logging
from pathlib import Path
from typing import Optional
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")
logger = logging.getLogger(__name__)

import runtime_state as _rt

def _is_mock() -> bool:
    return _rt.is_mock_mode()

def _headless() -> bool:
    return os.environ.get("PLAYWRIGHT_HEADLESS", "true").lower() != "false"

WA_URL = "https://web.whatsapp.com/"
USER_DATA_DIR = Path(os.environ.get("WA_PROFILE_DIR", "/tmp/whatsapp_profile"))
POLL_INTERVAL = float(os.environ.get("WA_POLL_INTERVAL", "3"))
QR_REFRESH_SECS = float(os.environ.get("WA_QR_REFRESH_SECS", "5"))


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


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class WhatsAppService:
    def __init__(self):
        self._playwright = None
        self._browser = None
        self._page = None
        self._connected = False
        self._qr_data_url: Optional[str] = None
        self._group_names: list[str] = []
        self._monitor_task: Optional[asyncio.Task] = None
        self._boot_task: Optional[asyncio.Task] = None
        self._on_image_callback = None
        self._seen_by_group: dict[str, set[str]] = {}
        self._last_error: Optional[str] = None
        self._active_group: Optional[str] = None
        self._step: str = "idle"
        self._prompt: Optional[str] = None
        self._screenshot_b64: Optional[str] = None
        self._updated_at: str = _now()

    # ---------- public getters ----------
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

    def status(self) -> dict:
        return {
            "step": self._step,
            "connected": self._connected,
            "qr_data_url": self._qr_data_url,
            "screenshot_b64": self._screenshot_b64,
            "prompt": self._prompt,
            "last_error": self._last_error,
            "groups": list(self._group_names),
            "active_group": self._active_group,
            "updated_at": self._updated_at,
            "headless": _headless(),
            "mock": _is_mock(),
        }

    # ---------- helpers ----------
    def _set(self, *, step: Optional[str] = None, prompt: Optional[str] = None,
             error: Optional[str] = None, qr: Optional[str] = None):
        if step is not None:
            self._step = step
        if prompt is not None:
            self._prompt = prompt or None
        if error is not None:
            self._last_error = error or None
        if qr is not None:
            self._qr_data_url = qr or None
        self._updated_at = _now()
        logger.info("[WA] step=%s prompt=%r error=%r", self._step, self._prompt, self._last_error)

    async def _snap(self):
        """Take a full-page screenshot and store it (best effort)."""
        if not self._page:
            return
        try:
            png = await self._page.screenshot(type="png", full_page=False)
            self._screenshot_b64 = base64.b64encode(png).decode()
            self._updated_at = _now()
        except Exception:
            pass

    # ---------- lifecycle ----------
    async def start(self, group_names, on_image):
        # Cancel any prior boot task
        if self._boot_task and not self._boot_task.done():
            self._boot_task.cancel()
        if self._monitor_task and not self._monitor_task.done():
            self._monitor_task.cancel()

        if isinstance(group_names, str):
            group_names = [g.strip() for g in group_names.split(",") if g.strip()]
        self._group_names = [g for g in (group_names or []) if g]
        self._on_image_callback = on_image
        self._seen_by_group = {g: set() for g in self._group_names}
        self._last_error = None
        self._prompt = None
        self._screenshot_b64 = None

        if _is_mock():
            self._connected = True
            self._qr_data_url = None
            self._set(step="connected", prompt=None, error=None)
            logger.info("WhatsApp (mock) started for groups %s", self._group_names)
            return {"ok": True, "mock": True, "groups": self._group_names, "step": "connected"}

        # Kick off boot in the background — return immediately so the UI can poll status.
        self._set(step="launching", prompt="Starting Chromium and opening WhatsApp Web…", error=None)
        self._boot_task = asyncio.create_task(self._boot())
        return {
            "ok": True,
            "groups": self._group_names,
            "step": self._step,
            "message": "WhatsApp is starting. Watch the live status panel — a QR will appear when it's ready.",
        }

    async def _boot(self):
        """Launch Playwright, open WA, keep refreshing the QR until linked or aborted."""
        try:
            from playwright.async_api import async_playwright
            self._playwright = await async_playwright().start()
            USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
            self._browser = await self._playwright.chromium.launch_persistent_context(
                str(USER_DATA_DIR),
                headless=_headless(),
                args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
            )
            self._page = self._browser.pages[0] if self._browser.pages else await self._browser.new_page()
            self._set(step="launching", prompt="Loading web.whatsapp.com…")
            await self._page.goto(WA_URL, wait_until="domcontentloaded", timeout=60000)
            await self._snap()

            # QR + linking loop (up to 5 minutes)
            deadline = asyncio.get_event_loop().time() + 300
            linked = False
            while asyncio.get_event_loop().time() < deadline:
                # Are we linked yet?
                chatlist = await self._page.query_selector(
                    '[data-testid="chat-list"], [aria-label="Chat list"], div#pane-side'
                )
                if chatlist:
                    linked = True
                    break

                # Is a QR present?
                qr_canvas = await self._page.query_selector('canvas[aria-label*="Scan"], canvas[aria-label*="scan"]')
                if qr_canvas:
                    try:
                        png = await qr_canvas.screenshot(type="png")
                        self._qr_data_url = "data:image/png;base64," + base64.b64encode(png).decode()
                        self._set(step="awaiting_qr",
                                  prompt="Open WhatsApp on your phone → Settings → Linked devices → Link a device → scan this QR.")
                        await self._snap()
                    except Exception:
                        pass
                else:
                    # Might be showing "loading" or "click to reload" — capture a snap for the user.
                    if self._step == "launching":
                        self._set(prompt="Waiting for the WhatsApp login screen…")
                    await self._snap()

                await asyncio.sleep(QR_REFRESH_SECS)

            if not linked:
                self._connected = False
                self._set(step="error", error="QR was not scanned in 5 minutes. Click Start again to try once more.",
                          prompt=None, qr="")
                await self._snap()
                return

            # We're linked.
            self._qr_data_url = None
            self._connected = True
            self._set(step="connected", prompt=None, error=None, qr="")
            await self._snap()
            self._monitor_task = asyncio.create_task(self._monitor_loop())
        except asyncio.CancelledError:
            return
        except Exception as e:
            logger.exception("WA boot failed")
            self._set(step="error", error=f"{type(e).__name__}: {e}", prompt=None)
            try:
                await self._snap()
            except Exception:
                pass
            await self._cleanup()

    async def _monitor_loop(self):
        """Rotate through configured groups, poll for new image messages."""
        idx = 0
        while True:
            try:
                await asyncio.sleep(POLL_INTERVAL)
                if not self._page or not self._connected or not self._group_names:
                    continue
                group = self._group_names[idx % len(self._group_names)]
                idx += 1
                self._active_group = group
                self._updated_at = _now()

                open_res = await self._page.evaluate(_JS_OPEN_GROUP, group)
                if not open_res.get("ok"):
                    logger.warning("Could not open group %s: %s", group, open_res.get("reason"))
                    continue
                await asyncio.sleep(1.2)

                messages = await self._page.evaluate(_JS_EXTRACT_IMAGES)
                seen = self._seen_by_group.setdefault(group, set())
                first_visit = not seen
                if first_visit:
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
        if _is_mock():
            logger.info("[MOCK WA reply] msg=%s -> %s", message_id, text)
            return True
        if not self._page:
            return False
        try:
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

    async def _cleanup(self):
        try:
            if self._browser:
                await self._browser.close()
            if self._playwright:
                await self._playwright.stop()
        except Exception:
            pass
        self._page = None
        self._browser = None
        self._playwright = None

    async def stop(self):
        if self._monitor_task:
            self._monitor_task.cancel()
        if self._boot_task:
            self._boot_task.cancel()
        await self._cleanup()
        self._connected = False
        self._qr_data_url = None
        self._set(step="stopped", prompt=None, error=None, qr="")


whatsapp_service = WhatsAppService()
