"""Iteration 2 tests: duplicate guard, digest, cron endpoint, settings additions,
plus a lightweight regression pass over iteration_1 endpoints."""
import io
import os
import time
import uuid
import pytest
import requests
from pathlib import Path
from PIL import Image, ImageDraw

# Load backend env for WEBHOOK_CRON_SECRET
from dotenv import load_dotenv
load_dotenv(Path("/app/backend/.env"))

BASE_URL = os.environ["REACT_APP_BACKEND_URL"].rstrip("/") if os.environ.get("REACT_APP_BACKEND_URL") else None
if not BASE_URL:
    # fallback to reading frontend .env
    from dotenv import dotenv_values
    v = dotenv_values("/app/frontend/.env")
    BASE_URL = v["REACT_APP_BACKEND_URL"].rstrip("/")

WEBHOOK_CRON_SECRET = os.environ["WEBHOOK_CRON_SECRET"]
API = f"{BASE_URL}/api"


def _make_receipt(utr: str = "540298761234", amount: str = "1500") -> bytes:
    img = Image.new("RGB", (600, 400), "white")
    d = ImageDraw.Draw(img)
    d.text((20, 20), "Google Pay", fill="black")
    d.text((20, 60), f"Amount: Rs {amount}", fill="black")
    d.text((20, 100), f"UTR: {utr}", fill="black")
    d.text((20, 140), "Paid to: Merchant", fill="black")
    d.text((20, 180), "Payer: Test User", fill="black")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture(scope="module")
def sess():
    s = requests.Session()
    return s


# ------------------------- Regression (iteration 1) -------------------------
class TestRegressionIter1:
    def test_root(self, sess):
        r = sess.get(f"{API}/")
        assert r.status_code == 200
        assert r.json().get("service") == "PayVerify Bot"

    def test_status(self, sess):
        r = sess.get(f"{API}/status")
        assert r.status_code == 200
        assert r.json().get("mock_mode") is True

    def test_settings_get(self, sess):
        r = sess.get(f"{API}/settings")
        assert r.status_code == 200
        j = r.json()
        assert "group_name" in j
        assert "reply_template_duplicate" in j
        assert "owner_email" in j
        assert "digest_enabled" in j

    def test_process_screenshot_even(self, sess):
        img = _make_receipt("540298761234")
        r = sess.post(f"{API}/process-screenshot",
                      files={"file": ("r.png", img, "image/png")},
                      data={"sender": "TEST_regress_even"})
        assert r.status_code == 200, r.text
        j = r.json()
        # First upload with this UTR could be received OR duplicate depending on state.
        assert j["status"] in ("received", "duplicate")
        assert j["utr_last4"] == "1234"


# ------------------------- Settings new fields -------------------------
class TestSettingsNewFields:
    def test_put_and_get_new_fields(self, sess):
        payload = {
            "id": "singleton",
            "group_name": "TEST Group",
            "gpay_email": "test@example.com",
            "owner_email": "delivered@resend.dev",
            "owner_name": "TEST Owner",
            "digest_enabled": True,
            "auto_reply": True,
            "reply_template_success": "✅ Received. UTR ...{utr_last4} ({amount})",
            "reply_template_fail": "❌ Not received. UTR ...{utr_last4} not found.",
            "reply_template_duplicate": "⚠️ Duplicate UTR ...{utr_last4} — already submitted by {orig_sender} on {orig_time}.",
        }
        r = sess.put(f"{API}/settings", json=payload)
        assert r.status_code == 200, r.text
        j = r.json()
        assert j["owner_email"] == "delivered@resend.dev"
        assert j["owner_name"] == "TEST Owner"
        assert j["digest_enabled"] is True
        assert "{orig_sender}" in j["reply_template_duplicate"]

        # GET again
        g = sess.get(f"{API}/settings").json()
        assert g["owner_email"] == "delivered@resend.dev"
        assert g["digest_enabled"] is True


# ------------------------- Duplicate guard -------------------------
class TestDuplicateGuard:
    def test_duplicate_flow(self, sess):
        # Unique UTR ending in even digit so first is 'received'
        utr = "5402" + str(int(time.time()))[-8:]  # 12 digits
        # ensure even last digit
        if int(utr[-1]) % 2 != 0:
            utr = utr[:-1] + "2"
        last4 = utr[-4:]

        # First upload — should be received
        img = _make_receipt(utr)
        r1 = sess.post(f"{API}/process-screenshot",
                       files={"file": ("r1.png", img, "image/png")},
                       data={"sender": "TEST_orig_sender"})
        assert r1.status_code == 200, r1.text
        j1 = r1.json()
        assert j1["status"] == "received", j1
        assert j1["utr_last4"] == last4
        orig_id = j1["id"]

        # Second upload same UTR — should be duplicate
        img2 = _make_receipt(utr)
        r2 = sess.post(f"{API}/process-screenshot",
                       files={"file": ("r2.png", img2, "image/png")},
                       data={"sender": "TEST_second_sender"})
        assert r2.status_code == 200, r2.text
        j2 = r2.json()
        assert j2["status"] == "duplicate", j2
        assert j2["duplicate_of"] == orig_id
        # GPay verify was skipped
        gp = j2.get("gpay_result") or {}
        assert gp.get("reason") == "duplicate" or "found" not in gp, gp
        # Reply substitutes placeholders (original sender + a time-ish string)
        assert "TEST_orig_sender" in j2["reply_text"], j2["reply_text"]
        assert last4 in j2["reply_text"]
        # rough date-like substring check
        assert any(c.isdigit() for c in j2["reply_text"])

    def test_stats_has_duplicate_field(self, sess):
        r = sess.get(f"{API}/transactions/stats")
        assert r.status_code == 200
        j = r.json()
        assert "duplicate" in j
        assert j["duplicate"] >= 1

    def test_list_duplicate_filter(self, sess):
        r = sess.get(f"{API}/transactions", params={"status_filter": "duplicate", "limit": 50})
        assert r.status_code == 200
        arr = r.json()
        assert isinstance(arr, list)
        assert len(arr) >= 1
        assert all(t["status"] == "duplicate" for t in arr)


# ------------------------- Digest -------------------------
class TestDigest:
    def test_digest_disabled_returns_not_sent(self, sess):
        # Temporarily disable digest
        cur = sess.get(f"{API}/settings").json()
        cur["digest_enabled"] = False
        sess.put(f"{API}/settings", json=cur)
        r = sess.post(f"{API}/digest/send-now")
        assert r.status_code == 200, r.text
        j = r.json()
        assert j["sent"] is False
        assert "reason" in j

    def test_digest_empty_owner_email_not_sent(self, sess):
        cur = sess.get(f"{API}/settings").json()
        cur["digest_enabled"] = True
        cur["owner_email"] = ""
        sess.put(f"{API}/settings", json=cur)
        r = sess.post(f"{API}/digest/send-now")
        assert r.status_code == 200
        j = r.json()
        assert j["sent"] is False

    def test_digest_send_now_delivered(self, sess):
        cur = sess.get(f"{API}/settings").json()
        cur["digest_enabled"] = True
        cur["owner_email"] = "delivered@resend.dev"
        cur["owner_name"] = "TEST Owner"
        sess.put(f"{API}/settings", json=cur)
        r = sess.post(f"{API}/digest/send-now")
        # Platform email proxy may sometimes 502 — surface it but don't crash suite
        assert r.status_code in (200, 502, 500), r.text
        if r.status_code != 200:
            pytest.skip(f"email proxy returned {r.status_code}: {r.text[:200]}")
        j = r.json()
        assert j["sent"] is True, j
        assert j["to"] == "delivered@resend.dev"
        assert j.get("email_id")
        assert isinstance(j.get("stats"), dict)

    def test_digest_history(self, sess):
        r = sess.get(f"{API}/digest/history")
        assert r.status_code == 200
        arr = r.json()
        assert isinstance(arr, list)
        # Might be empty if send failed
        if arr:
            # most recent first
            ts = [d["sent_at"] for d in arr]
            assert ts == sorted(ts, reverse=True)


# ------------------------- Cron endpoint -------------------------
class TestCronDigest:
    def test_no_auth_401(self, sess):
        r = sess.post(f"{API}/cron/digest")
        assert r.status_code == 401

    def test_wrong_token_401(self, sess):
        r = sess.post(f"{API}/cron/digest",
                      headers={"Authorization": "Bearer wrong-token-abc"})
        assert r.status_code == 401

    def test_correct_token_and_fast_ack(self, sess):
        wid = str(uuid.uuid4())
        t0 = time.time()
        r = sess.post(f"{API}/cron/digest",
                      headers={"Authorization": f"Bearer {WEBHOOK_CRON_SECRET}",
                               "X-Webhook-Id": wid})
        elapsed = time.time() - t0
        assert r.status_code == 200, r.text
        j = r.json()
        assert j["ok"] is True
        assert j.get("run_id") == wid
        # Fast ack (<2s) — background does the actual work
        assert elapsed < 2.5, f"cron endpoint too slow: {elapsed:.2f}s"

    def test_idempotency_same_webhook_id(self, sess):
        wid = str(uuid.uuid4())
        h = {"Authorization": f"Bearer {WEBHOOK_CRON_SECRET}", "X-Webhook-Id": wid}
        r1 = sess.post(f"{API}/cron/digest", headers=h)
        assert r1.status_code == 200
        assert r1.json().get("duplicate") is not True
        r2 = sess.post(f"{API}/cron/digest", headers=h)
        assert r2.status_code == 200
        assert r2.json().get("duplicate") is True
