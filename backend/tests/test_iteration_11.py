"""Iteration 11 — WhatsApp/GPay live status endpoints + diagnostics detail."""
import os
import time
import pytest
import requests

BASE_URL = os.environ["REACT_APP_BACKEND_URL"].rstrip("/")
API = f"{BASE_URL}/api"


@pytest.fixture(scope="module")
def client():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    # Baseline: mock mode
    s.post(f"{API}/mode", json={"mock_mode": True}, timeout=15)
    yield s
    # Teardown: restore mock mode
    try:
        s.post(f"{API}/mode", json={"mock_mode": True}, timeout=15)
    except Exception:
        pass


# ---- Regression smoke ----
def test_root(client):
    r = client.get(f"{API}/", timeout=10)
    assert r.status_code == 200
    assert r.json().get("service") == "PayVerify Bot"


def test_transactions_list(client):
    r = client.get(f"{API}/transactions?limit=5", timeout=10)
    assert r.status_code == 200
    assert isinstance(r.json(), list)


# ---- WhatsApp start requires groups ----
def test_whatsapp_start_requires_groups(client):
    # Save current settings, clear groups, expect 400, then restore
    s0 = client.get(f"{API}/settings", timeout=10).json()
    original_groups = s0.get("group_names") or []
    original_group_name = s0.get("group_name") or ""

    # Clear groups
    payload = {**s0, "group_names": [], "group_name": ""}
    r = client.put(f"{API}/settings", json=payload, timeout=15)
    assert r.status_code == 200

    r = client.post(f"{API}/whatsapp/start", timeout=15)
    assert r.status_code == 400
    body = r.json()
    detail = body.get("detail") or body.get("error") or ""
    assert "Add at least one WhatsApp group" in str(detail), f"got: {body}"

    # Restore
    restore = {**s0, "group_names": original_groups or ["TestGroup"], "group_name": original_group_name}
    r = client.put(f"{API}/settings", json=restore, timeout=15)
    assert r.status_code == 200
    s1 = client.get(f"{API}/settings", timeout=10).json()
    assert (s1.get("group_names") or []) == (original_groups or ["TestGroup"])


def test_whatsapp_start_mock_returns_connected(client):
    # Ensure mock mode + groups
    client.post(f"{API}/mode", json={"mock_mode": True}, timeout=10)
    s0 = client.get(f"{API}/settings", timeout=10).json()
    if not s0.get("group_names"):
        client.put(f"{API}/settings", json={**s0, "group_names": ["TestGroup"]}, timeout=15)

    r = client.post(f"{API}/whatsapp/start", timeout=20)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("ok") is True
    assert body.get("mock") is True
    assert body.get("step") == "connected"
    assert isinstance(body.get("groups"), list) and len(body["groups"]) >= 1


def test_whatsapp_status_shape(client):
    r = client.get(f"{API}/whatsapp/status", timeout=10)
    assert r.status_code == 200
    body = r.json()
    expected_keys = {"step", "connected", "qr_data_url", "screenshot_b64",
                     "prompt", "last_error", "groups", "active_group",
                     "updated_at", "headless", "mock"}
    missing = expected_keys - set(body.keys())
    assert not missing, f"missing keys: {missing}"
    assert body["step"] == "connected"
    assert body["connected"] is True
    assert body["mock"] is True


# ---- GPay status endpoint ----
def test_gpay_status_unknown_id_returns_404(client):
    r = client.get(f"{API}/gpay/accounts/does-not-exist-xyz/status", timeout=10)
    assert r.status_code == 404


def test_gpay_add_mock_and_status_shape(client):
    # ensure mock mode
    client.post(f"{API}/mode", json={"mock_mode": True}, timeout=10)
    payload = {"label": "TEST_iter11", "email": "TEST_iter11@example.com", "password": "pw"}
    r = client.post(f"{API}/gpay/accounts", json=payload, timeout=30)
    assert r.status_code == 200, r.text
    body = r.json()
    acct_id = body.get("account", {}).get("id")
    assert acct_id
    assert body["login"].get("mock") is True
    assert body["login"].get("step") == "logged_in"

    # small wait for state to settle
    time.sleep(0.5)

    # list_accounts shows is_logged_in=true
    accounts = client.get(f"{API}/gpay/accounts", timeout=10).json()
    match = next((a for a in accounts if a["id"] == acct_id), None)
    assert match is not None
    assert match.get("is_logged_in") is True

    # status endpoint shape
    st = client.get(f"{API}/gpay/accounts/{acct_id}/status", timeout=10)
    assert st.status_code == 200
    sb = st.json()
    expected = {"step", "is_logged_in", "prompt", "last_error",
                "current_url", "screenshot_b64", "updated_at"}
    missing = expected - set(sb.keys())
    assert not missing, f"missing: {missing}"
    assert sb["step"] == "logged_in"
    assert sb["is_logged_in"] is True

    # cleanup
    d = client.delete(f"{API}/gpay/accounts/{acct_id}", timeout=10)
    assert d.status_code == 200


# ---- Diagnostics ----
def test_diagnostics_mock_mode(client):
    client.post(f"{API}/mode", json={"mock_mode": True}, timeout=10)
    r = client.get(f"{API}/diagnostics", timeout=60)
    assert r.status_code == 200
    body = r.json()
    checks = body["checks"]
    assert len(checks) == 6
    names = [c["name"] for c in checks]
    for expected in ["MongoDB", "Gemini OCR", "Playwright / Chromium",
                     "GPay account pool", "WhatsApp service", "Email service (Resend)"]:
        assert expected in names, f"missing check {expected}"
    # In mock mode Playwright says skipped
    pw = next(c for c in checks if c["name"] == "Playwright / Chromium")
    assert pw["ok"] is True
    assert "skipped" in pw["detail"].lower() and "mock" in pw["detail"].lower()
    # All should be ok
    for c in checks:
        assert c["ok"] is True, f"{c['name']} failed: {c['detail']}"


def test_diagnostics_live_mode_playwright(client):
    # flip to live and re-run diagnostics; expect Playwright to actually launch
    r = client.post(f"{API}/mode", json={"mock_mode": False}, timeout=10)
    assert r.status_code == 200
    try:
        d = client.get(f"{API}/diagnostics", timeout=120)
        assert d.status_code == 200
        body = d.json()
        assert body["system"]["mock_mode"] is False
        pw = next(c for c in body["checks"] if c["name"] == "Playwright / Chromium")
        # Should now actually launch (or fail with a real error we can report)
        assert "skipped" not in (pw.get("detail") or "").lower()
        if pw["ok"]:
            assert "chromium" in pw["detail"].lower()
        else:
            # Not a test failure per se — report detail so main agent sees it
            print(f"NOTE: Live Playwright check failed: {pw['detail']}")
    finally:
        client.post(f"{API}/mode", json={"mock_mode": True}, timeout=10)


# ---- Regression: /process-screenshot & /transactions ----
def test_process_screenshot_regression(client):
    tiny_png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xff"
        b"\xff?\x03\x00\x08\xfc\x02\xfe\xa7v\x8d\xba\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    files = {"file": ("t.png", tiny_png, "image/png")}
    data = {"sender": "TEST_iter11", "auto_reply": "false"}
    # remove default JSON header for multipart
    s2 = requests.Session()
    r = s2.post(f"{API}/process-screenshot", files=files, data=data, timeout=60)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "id" in body or "status" in body or "utr" in body


def test_final_state_mock(client):
    assert client.get(f"{API}/mode", timeout=10).json()["mock_mode"] is True
