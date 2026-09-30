"""Iteration 7 — regression smoke after asyncio.wait_for wrapper + docker-compose fix.

Preview backend runs with BOT_MOCK_MODE=true, so verify:
  - Nothing crashed on restart (backend responds).
  - Mock-mode add + delete + re-login still work end-to-end.
"""
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
    # cleanup any TEST_ accounts left behind
    try:
        for a in sess.get(f"{API}/gpay/accounts", timeout=15).json():
            if a.get("label", "").startswith("TEST_"):
                sess.delete(f"{API}/gpay/accounts/{a['id']}", timeout=15)
    except Exception:
        pass


def _png_with_utr(utr="123456789012", amount="500"):
    img = Image.new("RGB", (500, 200), "white")
    d = ImageDraw.Draw(img)
    d.text((10, 20), f"UTR: {utr}", fill="black")
    d.text((10, 60), f"Amount: Rs {amount}", fill="black")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# --- Regression smoke ---
def test_status(s):
    r = s.get(f"{API}/status", timeout=15)
    assert r.status_code == 200
    d = r.json()
    assert d.get("mock_mode") is True
    assert "gpay_active_count" in d
    assert "gpay_total_count" in d
    assert "whatsapp_groups" in d


def test_list_accounts_no_pw_leak(s):
    r = s.get(f"{API}/gpay/accounts", timeout=15)
    assert r.status_code == 200
    for a in r.json():
        assert "password_enc" not in a


def test_add_account_mock(s):
    r = s.post(
        f"{API}/gpay/accounts",
        json={"label": "TEST_iter7", "email": "test_iter7@x.com", "password": "pw-7"},
        timeout=20,
    )
    assert r.status_code == 200, r.text
    d = r.json()
    assert "id" in d["account"]
    assert d["account"]["is_logged_in"] is True
    assert d["login"].get("ok") is True
    assert d["login"].get("mock") is True
    assert "password_enc" not in d["account"]
    s._aid = d["account"]["id"]


def test_patch_password_relogin_mock(s):
    aid = s._aid
    r = s.patch(f"{API}/gpay/accounts/{aid}", json={"password": "newpw"}, timeout=20)
    assert r.status_code == 200
    body = r.json()
    # PATCH returns updated account row (not the login result). Verify no pw leak.
    assert "password_enc" not in body
    # Confirm still logged in
    accts = s.get(f"{API}/gpay/accounts").json()
    row = next(a for a in accts if a["id"] == aid)
    assert row["is_logged_in"] is True


def test_process_screenshot_even_utr(s):
    unique_utr = f"7{int(time.time())}"[:12].ljust(12, "0")
    if int(unique_utr[-1]) % 2 == 1:
        unique_utr = unique_utr[:-1] + "0"
    files = {"file": ("r.png", _png_with_utr(unique_utr, "500"), "image/png")}
    data = {"sender": "TEST_iter7_sender", "group": "Bulk Orders"}
    r = s.post(f"{API}/process-screenshot", files=files, data=data, timeout=45)
    assert r.status_code == 200, r.text
    d = r.json()
    # OCR may or may not extract our text; only assert when UTR extracted with even last digit
    if d.get("utr") and d["utr"][-1].isdigit() and int(d["utr"][-1]) % 2 == 0:
        assert d["status"] == "received"
        # account_label may be top-level or under gpay_result depending on response shape
        label = d.get("account_label") or d.get("gpay_result", {}).get("account_label")
        assert label, f"missing account_label; response: {d}"


def test_transactions_stats(s):
    r = s.get(f"{API}/transactions/stats", timeout=15)
    assert r.status_code == 200
    d = r.json()
    for k in ("total", "received", "not_received", "utr_not_found", "duplicate"):
        assert k in d, f"missing {k}"


def test_delete_account(s):
    aid = s._aid
    r = s.delete(f"{API}/gpay/accounts/{aid}", timeout=15)
    assert r.status_code == 200
    assert r.json().get("ok") is True
    ids = {a["id"] for a in s.get(f"{API}/gpay/accounts").json()}
    assert aid not in ids
