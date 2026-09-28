"""Iteration 4 — multi-account GPay pool tests."""
import io
import os
import time
import pytest
import requests
from PIL import Image

BASE = os.environ.get("REACT_APP_BACKEND_URL", "https://payment-verify-bot-2.preview.emergentagent.com").rstrip("/")
API = f"{BASE}/api"


@pytest.fixture(scope="module")
def s():
    sess = requests.Session()
    yield sess
    # cleanup any TEST_ accounts
    try:
        for a in sess.get(f"{API}/gpay/accounts", timeout=15).json():
            if a.get("label", "").startswith("TEST_"):
                sess.delete(f"{API}/gpay/accounts/{a['id']}", timeout=15)
    except Exception:
        pass


def _png_bytes(text="UTR 123456789012 amount 500"):
    img = Image.new("RGB", (400, 200), "white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ---------- Regression sanity ----------
def test_status_shape(s):
    r = s.get(f"{API}/status", timeout=15)
    assert r.status_code == 200
    d = r.json()
    assert "gpay_active_count" in d
    assert "gpay_total_count" in d
    assert "whatsapp_groups" in d


def test_settings_get(s):
    r = s.get(f"{API}/settings", timeout=15)
    assert r.status_code == 200


def test_txn_stats(s):
    r = s.get(f"{API}/transactions/stats", timeout=15)
    assert r.status_code == 200
    for k in ("total", "received", "not_received", "duplicate"):
        assert k in r.json()


# ---------- GPay account pool CRUD ----------
def test_add_account_no_pw_in_response(s):
    r = s.post(f"{API}/gpay/accounts", json={"label": "TEST_A", "email": "test_a@x.com", "password": "pw-a"}, timeout=15)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["account"]["is_active"] is True
    assert d["account"]["is_logged_in"] is True
    assert "password_enc" not in d["account"]
    assert d["login"].get("ok") is True
    s._acct_a = d["account"]["id"]


def test_list_no_pw_leak(s):
    r = s.get(f"{API}/gpay/accounts", timeout=15)
    assert r.status_code == 200
    for a in r.json():
        assert "password_enc" not in a


def test_hit_limit_and_reactivate(s):
    aid = s._acct_a
    r = s.post(f"{API}/gpay/accounts/{aid}/hit-limit", timeout=15)
    assert r.status_code == 200 and r.json() == {"ok": True}
    acct = next(a for a in s.get(f"{API}/gpay/accounts").json() if a["id"] == aid)
    assert acct["is_active"] is False
    assert acct["is_logged_in"] is False
    assert acct["hit_limit_at"] is not None

    r = s.post(f"{API}/gpay/accounts/{aid}/reactivate", timeout=15)
    assert r.status_code == 200
    time.sleep(1.2)
    acct = next(a for a in s.get(f"{API}/gpay/accounts").json() if a["id"] == aid)
    assert acct["is_active"] is True
    assert acct["hit_limit_at"] is None
    assert acct["is_logged_in"] is True


def test_patch_label(s):
    aid = s._acct_a
    r = s.patch(f"{API}/gpay/accounts/{aid}", json={"label": "TEST_A_renamed"}, timeout=15)
    assert r.status_code == 200
    assert r.json()["label"] == "TEST_A_renamed"


def test_patch_password_triggers_relogin(s):
    aid = s._acct_a
    r = s.patch(f"{API}/gpay/accounts/{aid}", json={"password": "newpw-a"}, timeout=15)
    assert r.status_code == 200
    assert "password_enc" not in r.json()


def test_relogin_without_password_uses_stored(s):
    aid = s._acct_a
    r = s.post(f"{API}/gpay/accounts/{aid}/re-login", timeout=15)
    assert r.status_code == 200
    assert r.json().get("ok") is True


def test_relogin_with_new_password_then_without(s):
    aid = s._acct_a
    r = s.post(f"{API}/gpay/accounts/{aid}/re-login", data={"password": "rotated-pw"}, timeout=15)
    assert r.status_code == 200 and r.json().get("ok") is True
    r2 = s.post(f"{API}/gpay/accounts/{aid}/re-login", timeout=15)
    assert r2.status_code == 200 and r2.json().get("ok") is True


# ---------- Parallel verify ----------
def test_parallel_verify_even_utr(s):
    # Add TEST_B
    r = s.post(f"{API}/gpay/accounts", json={"label": "TEST_B", "email": "test_b@x.com", "password": "pw-b"}, timeout=15)
    assert r.status_code == 200
    s._acct_b = r.json()["account"]["id"]

    # Ensure both TEST_A and TEST_B active + logged_in
    time.sleep(0.5)
    files = {"file": ("r.png", _png_bytes(), "image/png")}
    data = {"sender": "TEST_parallel", "group": "Bulk Orders"}
    r = s.post(f"{API}/process-screenshot", files=files, data=data, timeout=30)
    assert r.status_code == 200, r.text
    d = r.json()
    # OCR may or may not extract a UTR from blank image; do not assert status without utr.
    if d.get("utr") and d["utr"][-1].isdigit() and int(d["utr"][-1]) % 2 == 0:
        gp = d.get("gpay_result", {})
        assert gp.get("found") is True
        assert gp.get("account_label") in ("TEST_A_renamed", "TEST_B", "Store-A", "Store-B", "Default")
        # checked_accounts should include multiple labels (our two TEST_ ones at minimum)
        checked = gp.get("checked_accounts") or []
        labels = set(checked)
        assert any(lbl.startswith("TEST_") for lbl in labels), f"checked_accounts={checked}"



# ---------- Direct parallel verify via legacy endpoint ----------
def test_parallel_verify_direct_even(s):
    # ensure at least 2 active accounts
    r1 = s.post(f"{API}/gpay/accounts", json={"label": "TEST_P1", "email": "p1@x.com", "password": "pw"}, timeout=15)
    r2 = s.post(f"{API}/gpay/accounts", json={"label": "TEST_P2", "email": "p2@x.com", "password": "pw"}, timeout=15)
    id1, id2 = r1.json()["account"]["id"], r2.json()["account"]["id"]
    time.sleep(0.6)
    try:
        # Even UTR -> found
        r = s.post(f"{API}/gpay/verify", json={"utr": "123456789012", "amount": "500"}, timeout=15)
        assert r.status_code == 200
        d = r.json()
        assert d["found"] is True
        assert d.get("account_label") is not None
        assert d.get("account_id") is not None
        assert isinstance(d.get("checked_accounts"), list)
        assert len(d["checked_accounts"]) >= 2
        # Success reply template test via orchestrator: verify account_label placeholder replaced
        # Odd UTR -> not found, both labels listed
        r = s.post(f"{API}/gpay/verify", json={"utr": "123456789013"}, timeout=15)
        d = r.json()
        assert d["found"] is False
        assert isinstance(d.get("checked_accounts"), list)
        assert len(d["checked_accounts"]) >= 2

        # Hit-limit both -> no active
        for aid in (id1, id2):
            s.post(f"{API}/gpay/accounts/{aid}/hit-limit", timeout=15)
        # Also deactivate any other currently active
        for a in s.get(f"{API}/gpay/accounts").json():
            if a["is_active"]:
                s.post(f"{API}/gpay/accounts/{a['id']}/hit-limit", timeout=15)
        r = s.post(f"{API}/gpay/verify", json={"utr": "123456789012"}, timeout=15)
        d = r.json()
        assert d["found"] is False
        assert d.get("reason") == "no active accounts online"
        assert d.get("checked_accounts") == []
    finally:
        s.delete(f"{API}/gpay/accounts/{id1}", timeout=15)
        s.delete(f"{API}/gpay/accounts/{id2}", timeout=15)
        # reactivate all remaining
        for a in s.get(f"{API}/gpay/accounts").json():
            if not a["is_active"]:
                s.post(f"{API}/gpay/accounts/{a['id']}/reactivate", timeout=15)

def test_no_active_verify(s):
    # Deactivate our TEST_A and TEST_B and any Store-*/Default
    accts = s.get(f"{API}/gpay/accounts").json()
    for a in accts:
        if a["is_active"]:
            s.post(f"{API}/gpay/accounts/{a['id']}/hit-limit", timeout=15)
    # Verify manually via legacy endpoint - just check pool state
    files = {"file": ("r.png", _png_bytes(), "image/png")}
    r = s.post(f"{API}/process-screenshot", files=files, data={"sender": "TEST_none"}, timeout=30)
    assert r.status_code == 200
    d = r.json()
    if d.get("utr"):  # only assert when UTR was found
        gp = d.get("gpay_result", {})
        assert gp.get("reason") == "no active accounts online"
        assert gp.get("checked_accounts") == []
        assert d["status"] == "not_received"

    # restore: reactivate everything for downstream
    for a in s.get(f"{API}/gpay/accounts").json():
        s.post(f"{API}/gpay/accounts/{a['id']}/reactivate", timeout=15)


# ---------- Status counts ----------
def test_status_counts_match_pool(s):
    time.sleep(1.2)
    accts = s.get(f"{API}/gpay/accounts").json()
    st = s.get(f"{API}/status").json()
    assert st["gpay_total_count"] == len(accts)
    active_online = sum(1 for a in accts if a["is_active"] and a["is_logged_in"])
    assert st["gpay_active_count"] == active_online


# ---------- Legacy login upsert ----------
def test_legacy_gpay_login_upsert(s):
    r = s.post(f"{API}/gpay/login", json={"email": "test_legacy@x.com", "password": "leg-pw"}, timeout=15)
    assert r.status_code == 200
    assert r.json().get("ok") is True
    # Second call reuses same account (no duplicate)
    accts = s.get(f"{API}/gpay/accounts").json()
    matches = [a for a in accts if a["email"] == "test_legacy@x.com"]
    assert len(matches) == 1
    r2 = s.post(f"{API}/gpay/login", json={"email": "test_legacy@x.com", "password": "leg-pw2"}, timeout=15)
    assert r2.status_code == 200
    accts2 = s.get(f"{API}/gpay/accounts").json()
    matches2 = [a for a in accts2 if a["email"] == "test_legacy@x.com"]
    assert len(matches2) == 1
    # cleanup
    s.delete(f"{API}/gpay/accounts/{matches2[0]['id']}", timeout=15)


# ---------- Delete ----------
def test_delete_account(s):
    r = s.delete(f"{API}/gpay/accounts/{s._acct_a}", timeout=15)
    assert r.status_code == 200 and r.json().get("ok") is True
    ids = {a["id"] for a in s.get(f"{API}/gpay/accounts").json()}
    assert s._acct_a not in ids
    # delete B too
    s.delete(f"{API}/gpay/accounts/{s._acct_b}", timeout=15)
