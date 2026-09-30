"""Iteration 8 — Verify SPA fallback re-architecture doesn't affect preview /api routing.

Preview does NOT have /app/frontend/build so spa_fallback is dead code. We assert:
- All previously-tested endpoints still respond 200.
- A random non-api path returns 404 (proving no SPA fallback swallowed it).
- /api/nonexistent returns 404 (router 404, not intercepted).
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
    try:
        for a in sess.get(f"{API}/gpay/accounts", timeout=15).json():
            if a.get("label", "").startswith("TEST_iter8"):
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


def test_api_root(s):
    r = s.get(f"{API}/", timeout=15)
    assert r.status_code == 200
    d = r.json()
    assert "mock_mode" in d


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
    assert isinstance(r.json(), list)
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
        json={"label": "TEST_iter8", "email": "test_iter8@x.com", "password": "pw8"},
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
    unique_utr = f"8{int(time.time())}"[:12].ljust(12, "0")
    if int(unique_utr[-1]) % 2 == 1:
        unique_utr = unique_utr[:-1] + "0"
    files = {"file": ("r.png", _png(unique_utr), "image/png")}
    data = {"sender": "TEST_iter8_sender", "group": "Bulk Orders"}
    r = s.post(f"{API}/process-screenshot", files=files, data=data, timeout=60)
    assert r.status_code == 200, r.text
    d = r.json()
    assert "status" in d


# --- Iteration 8 SPA fallback safety checks ---
def test_spa_fallback_dead_code_in_preview(s):
    """In preview, /app/frontend/build doesn't exist so backend's spa_fallback
    route is not registered. Non-/api paths on the ingress URL are routed to
    the frontend dev server (port 3000) which returns React's index.html.
    Either way, /api/* must NOT be swallowed — proven by other tests."""
    from pathlib import Path
    assert not Path("/app/frontend/build").exists(), "Preview should not have build dir"
    r = s.get(f"{BASE}/some/spa/route", timeout=15)
    # Preview ingress serves React dev-server HTML for non-api paths -> 200 html
    assert r.status_code == 200
    assert "<html" in r.text.lower() or "<!doctype" in r.text.lower()


def test_api_nonexistent_returns_404(s):
    r = s.get(f"{API}/definitely-not-a-real-endpoint", timeout=15)
    assert r.status_code == 404


def test_api_prefix_not_swallowed(s):
    """Sanity: hitting the fallback-shaped path /api/... still hits the router."""
    r = s.get(f"{BASE}/api/status", timeout=15)
    assert r.status_code == 200
