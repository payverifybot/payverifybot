"""Iteration 9 — Docker fix (no build-time impact on preview) + /api/diagnostics endpoint."""
import io
import os
import time
import pytest
import requests
from PIL import Image, ImageDraw

BASE = os.environ["REACT_APP_BACKEND_URL"].rstrip("/")
API = f"{BASE}/api"


@pytest.fixture(scope="module")
def s():
    sess = requests.Session()
    yield sess
    try:
        for a in sess.get(f"{API}/gpay/accounts", timeout=15).json():
            if a.get("label", "").startswith("TEST_iter9"):
                sess.delete(f"{API}/gpay/accounts/{a['id']}", timeout=15)
    except Exception:
        pass


def _png(utr="123456789012"):
    img = Image.new("RGB", (500, 200), "white")
    d = ImageDraw.Draw(img)
    d.text((10, 20), f"UTR: {utr}", fill="black")
    d.text((10, 60), "Amount: Rs 500", fill="black")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ---- Regression smoke ----
def test_api_root(s):
    r = s.get(f"{API}/", timeout=15)
    assert r.status_code == 200
    assert "mock_mode" in r.json()


def test_status(s):
    r = s.get(f"{API}/status", timeout=15)
    assert r.status_code == 200
    d = r.json()
    assert d.get("mock_mode") is True
    for k in ("gpay_active_count", "gpay_total_count", "whatsapp_groups"):
        assert k in d


def test_settings(s):
    r = s.get(f"{API}/settings", timeout=15)
    assert r.status_code == 200
    assert isinstance(r.json(), dict)


def test_gpay_accounts_list(s):
    r = s.get(f"{API}/gpay/accounts", timeout=15)
    assert r.status_code == 200
    for a in r.json():
        assert "password_enc" not in a


def test_transactions_stats(s):
    r = s.get(f"{API}/transactions/stats", timeout=15)
    assert r.status_code == 200
    d = r.json()
    for k in ("total", "received", "not_received", "utr_not_found", "duplicate"):
        assert k in d


def test_transactions_groups(s):
    r = s.get(f"{API}/transactions/groups", timeout=15)
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_add_and_delete_gpay(s):
    r = s.post(
        f"{API}/gpay/accounts",
        json={"label": "TEST_iter9", "email": "t9@x.com", "password": "pw9"},
        timeout=20,
    )
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["login"].get("ok") is True
    aid = d["account"]["id"]
    assert "password_enc" not in d["account"]
    dr = s.delete(f"{API}/gpay/accounts/{aid}", timeout=15)
    assert dr.status_code == 200
    assert dr.json().get("ok") is True


def test_process_screenshot_even_utr(s):
    unique_utr = f"9{int(time.time())}"[:12].ljust(12, "0")
    if int(unique_utr[-1]) % 2 == 1:
        unique_utr = unique_utr[:-1] + "0"
    files = {"file": ("r.png", _png(unique_utr), "image/png")}
    data = {"sender": "TEST_iter9_sender", "group": "Bulk Orders"}
    r = s.post(f"{API}/process-screenshot", files=files, data=data, timeout=60)
    assert r.status_code == 200, r.text
    assert "status" in r.json()


# ---- NEW: diagnostics endpoint ----
def test_diagnostics_shape(s):
    r = s.get(f"{API}/diagnostics", timeout=60)
    assert r.status_code == 200, r.text
    d = r.json()
    # top-level
    for k in ("when", "system", "env", "checks", "all_ok"):
        assert k in d, f"missing key {k}"
    # system
    for k in ("platform", "python", "mock_mode"):
        assert k in d["system"]
    assert d["system"]["mock_mode"] is True
    # env keys
    for k in ("MONGO_URL_set", "EMERGENT_LLM_KEY_set", "EMERGENT_EMAIL_KEY_set",
              "GPAY_ENC_KEY_set", "WEBHOOK_CRON_SECRET_set"):
        assert k in d["env"], f"missing env key {k}"
    # required checks by name
    names = [c["name"] for c in d["checks"]]
    for n in ("MongoDB", "Gemini OCR", "Playwright / Chromium",
              "GPay account pool", "WhatsApp service", "Email service (Resend)"):
        assert n in names, f"missing check '{n}' in {names}"
    for c in d["checks"]:
        assert isinstance(c["ok"], bool)
        assert "detail" in c


def test_diagnostics_playwright_mock_skipped(s):
    r = s.get(f"{API}/diagnostics", timeout=60)
    d = r.json()
    pw = next(c for c in d["checks"] if c["name"] == "Playwright / Chromium")
    assert pw["ok"] is True
    assert "skipped" in pw["detail"].lower() or "mock" in pw["detail"].lower()


def test_diagnostics_all_ok_in_mock(s):
    r = s.get(f"{API}/diagnostics", timeout=60)
    d = r.json()
    failing = [c for c in d["checks"] if not c["ok"]]
    assert d["all_ok"] is True, f"failing checks: {failing}"
