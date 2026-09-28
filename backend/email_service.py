"""Emergent-managed Resend email service (guardrail-gated).

Only used for internal transactional emails to the business owner
(daily payment digest). No user-supplied recipients or HTML — G4 safe.
"""
import os
import re
import ipaddress
import logging
import httpx
from html.parser import HTMLParser
from urllib.parse import urlparse
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")
logger = logging.getLogger(__name__)

EMAIL_BASE_URL = "https://integrations.emergentagent.com"
EMAIL_KEY = os.environ.get("EMERGENT_EMAIL_KEY", "")
EMAIL_FROM_NAME = os.environ.get("EMAIL_FROM_NAME", "PayVerify Bot")
EMAIL_REPLY_TO = os.environ.get("EMAIL_REPLY_TO")


# ---------- G2/G3 guardrail gate ----------
_SHORTENERS = ("bit.ly", "tinyurl.com", "t.co", "is.gd", "cutt.ly", "goo.gl", "rebrand.ly")
_CRED_ASK = (
    "reply with your password", "reply with the code", "send your password",
    "cvv", "send us your password", "enter your password below",
    "confirm your card number", "your full card number", "seed phrase",
    "recovery phrase", "verify your card", "social security number",
    "confirm your bank details",
)
_HOSTISH = re.compile(r"\b(?:https?://)?((?:[a-z0-9-]+\.)+[a-z]{2,})", re.I)


def _host_ok(host: str) -> bool:
    if not host or "xn--" in host:
        return False
    try:
        ipaddress.ip_address(host)
        return False
    except ValueError:
        pass
    return not any(host == s or host.endswith("." + s) for s in _SHORTENERS)


def _same_site(shown: str, real: str) -> bool:
    return shown == real or real.endswith("." + shown) or shown.endswith("." + real)


class _EmailScan(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags, self.urls, self.anchors = set(), [], []
        self._href, self._text = None, []

    def handle_starttag(self, tag, attrs):
        self.tags.add(tag.lower())
        self.urls += [v for k, v in attrs if k.lower() in ("href", "src") and v]
        if tag.lower() == "a":
            self._href = dict((k.lower(), v) for k, v in attrs).get("href")
            self._text = []

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag.lower() == "a" and self._href is not None:
            self.anchors.append((self._href, "".join(self._text)))
            self._href, self._text = None, []


def _assert_safe_email(subject: str, html: str) -> None:
    scan = _EmailScan(); scan.feed(html)
    if scan.tags & {"form", "input", "textarea", "select"}:
        raise ValueError("No forms or input fields in email (G2)")
    body = f"{subject}\n{html}".lower()
    for p in _CRED_ASK:
        if p in body:
            raise ValueError(f"Email asks for credentials: {p!r} (G2)")
    for url in scan.urls:
        low = url.strip().lower()
        if low.startswith(("mailto:", "tel:", "cid:", "#")):
            continue
        if not low.startswith("https://"):
            raise ValueError(f"Email links must be absolute https: {url!r} (G3)")
        host = urlparse(low).hostname or ""
        if not _host_ok(host) or urlparse(low).username is not None:
            raise ValueError(f"Shortened/IP/creds URL: {url!r} (G3)")
    for href, text in scan.anchors:
        real = urlparse(href.strip().lower()).hostname or ""
        if not real:
            continue
        for m in _HOSTISH.finditer(text):
            if not _same_site(m.group(1).lower(), real):
                raise ValueError(f"Anchor text {m.group(1)!r} != real host {real!r} (G3)")


async def send_email(*, to: str, subject: str, html: str) -> str | None:
    """Send a transactional email. Owner-scoped; recipient must be from server-side settings."""
    if not EMAIL_KEY:
        raise RuntimeError("EMERGENT_EMAIL_KEY not configured")
    _assert_safe_email(subject, html)
    payload = {
        "to": [to],
        "subject": subject,
        "html": html,
        "from_name": EMAIL_FROM_NAME,
    }
    if EMAIL_REPLY_TO:
        payload["contact_email"] = EMAIL_REPLY_TO

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{EMAIL_BASE_URL}/api/v1/email/send",
            headers={"X-Email-Key": EMAIL_KEY},
            json=payload,
        )
    if resp.status_code >= 400:
        logger.error("Email send failed: %s %s", resp.status_code, resp.text)
        resp.raise_for_status()
    return resp.json().get("id")


# ---------- Templates ----------
def render_digest_html(stats: dict, recent: list, owner_name: str = "there") -> str:
    """Build the daily digest email HTML from server-side data (no user input)."""
    from html import escape as h
    rows = ""
    for t in recent[:15]:
        color = {"received": "#10b981", "not_received": "#ef4444",
                 "utr_not_found": "#f59e0b", "duplicate": "#a78bfa"}.get(t.get("status"), "#71717a")
        rows += (
            f'<tr>'
            f'<td style="padding:8px 10px;border-bottom:1px solid #eee;font-family:Arial,sans-serif;font-size:13px;color:#333">{h(t.get("sender") or "-")}</td>'
            f'<td style="padding:8px 10px;border-bottom:1px solid #eee;font-family:Arial,sans-serif;font-size:12px;color:#666">{h(t.get("group") or "-")}</td>'
            f'<td style="padding:8px 10px;border-bottom:1px solid #eee;font-family:monospace;font-size:12px;color:#666">...{h(t.get("utr_last4") or "----")}</td>'
            f'<td style="padding:8px 10px;border-bottom:1px solid #eee;font-family:Arial,sans-serif;font-size:13px;color:#333">Rs {h(str(t.get("amount") or "-"))}</td>'
            f'<td style="padding:8px 10px;border-bottom:1px solid #eee;font-family:Arial,sans-serif;font-size:12px;color:{color};font-weight:600">{h((t.get("status") or "").replace("_"," "))}</td>'
            f'</tr>'
        )
    if not rows:
        rows = ('<tr><td colspan="5" style="padding:16px;text-align:center;'
                'font-family:Arial,sans-serif;font-size:13px;color:#999">'
                'No transactions processed today.</td></tr>')

    total = stats.get("total", 0)
    ok = stats.get("received", 0)
    bad = stats.get("not_received", 0)
    miss = stats.get("utr_not_found", 0)
    dup = stats.get("duplicate", 0)

    return (
        f'<table role="presentation" width="100%" style="background:#f4f4f5;padding:24px 0">'
        f'<tr><td align="center">'
        f'<table role="presentation" width="560" style="background:#fff;border-radius:10px;'
        f'overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,.06)">'
        f'<tr><td style="padding:24px 28px;background:#065f46;color:#fff;font-family:Arial,sans-serif">'
        f'<div style="font-size:12px;letter-spacing:2px;opacity:.75">DAILY PAYMENT DIGEST</div>'
        f'<div style="font-size:22px;font-weight:700;margin-top:4px">{h(EMAIL_FROM_NAME)}</div>'
        f'</td></tr>'
        f'<tr><td style="padding:24px 28px;font-family:Arial,sans-serif;font-size:14px;color:#333">'
        f'<p>Hi {h(owner_name)}, here is what your bot did today:</p>'
        f'<table role="presentation" width="100%" style="margin:16px 0">'
        f'<tr>'
        f'<td style="padding:10px;background:#f0fdf4;border:1px solid #bbf7d0;border-radius:6px;text-align:center">'
        f'<div style="font-size:11px;color:#065f46;letter-spacing:1px">RECEIVED</div>'
        f'<div style="font-size:28px;font-weight:700;color:#065f46">{ok}</div></td>'
        f'<td style="width:8px"></td>'
        f'<td style="padding:10px;background:#fef2f2;border:1px solid #fecaca;border-radius:6px;text-align:center">'
        f'<div style="font-size:11px;color:#991b1b;letter-spacing:1px">NOT RECEIVED</div>'
        f'<div style="font-size:28px;font-weight:700;color:#991b1b">{bad}</div></td>'
        f'<td style="width:8px"></td>'
        f'<td style="padding:10px;background:#fffbeb;border:1px solid #fde68a;border-radius:6px;text-align:center">'
        f'<div style="font-size:11px;color:#92400e;letter-spacing:1px">UNCLEAR</div>'
        f'<div style="font-size:28px;font-weight:700;color:#92400e">{miss + dup}</div></td>'
        f'</tr></table>'
        f'<p style="font-size:12px;color:#666">Total processed: <strong>{total}</strong> · Duplicate flagged: <strong>{dup}</strong></p>'
        f'<h3 style="margin-top:24px;margin-bottom:8px;font-size:14px;color:#111">Latest activity</h3>'
        f'<table role="presentation" width="100%" style="border-collapse:collapse;border-top:1px solid #eee">'
        f'{rows}'
        f'</table>'
        f'<p style="font-size:12px;color:#888;margin-top:24px;line-height:1.6">'
        f'Sent by {h(EMAIL_FROM_NAME)}. We never ask for your password or card details by email.'
        f'</p></td></tr></table></td></tr></table>'
    )
