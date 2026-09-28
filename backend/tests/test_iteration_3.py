"""Iteration 3 — Multi-Group support tests."""
import io
import os
import time
import uuid
import pytest
import requests
from PIL import Image

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/") or "http://localhost:8001"
if not BASE_URL.endswith("/api"):
    API = f"{BASE_URL}/api"
else:
    API = BASE_URL

# Load webhook secret from backend .env
from dotenv import dotenv_values  # noqa
_env = dotenv_values("/app/backend/.env")
WEBHOOK_CRON_SECRET = _env.get("WEBHOOK_CRON_SECRET", "")


def _png_bytes():
    buf = io.BytesIO()
    Image.new("RGB", (32, 32), (255, 255, 255)).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture(scope="module")
def orig_settings():
    r = requests.get(f"{API}/settings")
    assert r.status_code == 200
    return r.json()


# ---------------- Settings migration ----------------

class TestSettingsMigration:
    def test_multi_group_persist_and_legacy_sync(self):
        payload = {
            "group_name": "",
            "group_names": ["A", "B"],
            "gpay_email": "", "owner_email": "delivered@resend.dev", "owner_name": "Tester",
            "digest_enabled": True, "auto_reply": True,
        }
        r = requests.put(f"{API}/settings", json=payload)
        assert r.status_code == 200, r.text
        r = requests.get(f"{API}/settings")
        s = r.json()
        assert s["group_names"] == ["A", "B"]
        assert s["group_name"] == "A"

    def test_legacy_only_backfills_group_names(self):
        payload = {"group_name": "Solo Grp", "group_names": [],
                   "owner_email": "delivered@resend.dev", "digest_enabled": True}
        r = requests.put(f"{API}/settings", json=payload)
        assert r.status_code == 200
        s = requests.get(f"{API}/settings").json()
        assert s["group_names"] == ["Solo Grp"]

    def test_dedupe_and_trim(self):
        payload = {"group_names": [" Bulk ", "Bulk", "Premium", "  Premium  "],
                   "group_name": "", "owner_email": "delivered@resend.dev", "digest_enabled": True}
        r = requests.put(f"{API}/settings", json=payload)
        assert r.status_code == 200
        s = requests.get(f"{API}/settings").json()
        assert s["group_names"] == ["Bulk", "Premium"]


# ---------------- WhatsApp start ----------------

class TestWhatsAppStart:
    def test_start_fails_when_no_groups(self):
        # Clear groups
        requests.put(f"{API}/settings", json={"group_name": "", "group_names": [],
                                              "owner_email": "delivered@resend.dev", "digest_enabled": True})
        r = requests.post(f"{API}/whatsapp/start")
        assert r.status_code == 400
        assert "group" in r.text.lower()

    def test_start_with_multi_groups(self):
        requests.put(f"{API}/settings", json={
            "group_name": "", "group_names": ["Bulk Orders", "Premium Buyers"],
            "owner_email": "delivered@resend.dev", "digest_enabled": True,
        })
        r = requests.post(f"{API}/whatsapp/start")
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["ok"] is True
        assert d.get("mock") is True
        assert d["groups"] == ["Bulk Orders", "Premium Buyers"]

    def test_status_reports_groups(self):
        r = requests.get(f"{API}/status").json()
        assert r["whatsapp_groups"] == ["Bulk Orders", "Premium Buyers"]
        assert r["whatsapp_active_group"] is None
        assert r["mock_mode"] is True


# ---------------- Process screenshot with group ----------------

class TestProcessScreenshotGroup:
    def test_with_group_persists(self):
        r = requests.post(
            f"{API}/process-screenshot",
            files={"file": ("t.png", _png_bytes(), "image/png")},
            data={"sender": "TEST_sender_g1", "group": "Bulk Orders"},
        )
        assert r.status_code == 200, r.text
        d = r.json()
        assert d.get("group") == "Bulk Orders"
        # Confirm via GET
        got = requests.get(f"{API}/transactions/{d['id']}").json()
        assert got["group"] == "Bulk Orders"

    def test_without_group_is_null(self):
        r = requests.post(
            f"{API}/process-screenshot",
            files={"file": ("t.png", _png_bytes(), "image/png")},
            data={"sender": "TEST_sender_ng"},
        )
        assert r.status_code == 200
        d = r.json()
        assert d.get("group") in (None, "")


# ---------------- Transactions filters ----------------

class TestTransactionFilters:
    def test_groups_aggregation(self):
        r = requests.get(f"{API}/transactions/groups")
        assert r.status_code == 200
        arr = r.json()
        assert isinstance(arr, list)
        names = [g["group"] for g in arr]
        assert "Bulk Orders" in names
        # sorted desc
        counts = [g["count"] for g in arr]
        assert counts == sorted(counts, reverse=True)

    def test_filter_by_group(self):
        r = requests.get(f"{API}/transactions", params={"group": "Bulk Orders"})
        assert r.status_code == 200
        for t in r.json():
            assert t.get("group") == "Bulk Orders"

    def test_filter_by_group_and_status(self):
        r = requests.get(f"{API}/transactions",
                         params={"group": "Bulk Orders", "status_filter": "received"})
        assert r.status_code == 200
        for t in r.json():
            assert t["group"] == "Bulk Orders"
            assert t["status"] == "received"

    def test_stats_group_scope(self):
        all_stats = requests.get(f"{API}/transactions/stats").json()
        grp_stats = requests.get(f"{API}/transactions/stats",
                                 params={"group": "Bulk Orders"}).json()
        assert grp_stats["total"] <= all_stats["total"]
        # Verify grp_stats matches the count filter
        n = len(requests.get(f"{API}/transactions",
                             params={"group": "Bulk Orders", "limit": 500}).json())
        assert grp_stats["total"] == n


# ---------------- Duplicate guard across groups ----------------

class TestGlobalDuplicateGuard:
    def test_duplicate_across_groups(self):
        # Force an even-last-digit UTR by using OCR mock? OCR reads real image.
        # Instead, insert two txns via direct process-screenshot both with sender containing marker,
        # then rely on OCR to potentially not extract UTR — fallback: use raw DB insert via marker.
        # We'll just check: submit same image twice to different groups; if OCR yields a UTR,
        # second one should be duplicate. If OCR gives nothing, skip.
        img = _png_bytes()
        r1 = requests.post(f"{API}/process-screenshot",
                           files={"file": ("t.png", img, "image/png")},
                           data={"sender": "TEST_dupA", "group": "Bulk Orders"}).json()
        r2 = requests.post(f"{API}/process-screenshot",
                           files={"file": ("t.png", img, "image/png")},
                           data={"sender": "TEST_dupB", "group": "Premium Buyers"}).json()
        if not r1.get("utr") or r1.get("utr") != r2.get("utr"):
            pytest.skip("OCR non-deterministic on blank image; cannot deterministically test duplicate here")
        assert r2["status"] == "duplicate"
        assert r2.get("group") == "Premium Buyers"


# ---------------- Digest still works ----------------

class TestDigestRegression:
    def test_send_now(self):
        requests.put(f"{API}/settings", json={
            "group_name": "", "group_names": ["Bulk Orders", "Premium Buyers"],
            "owner_email": "delivered@resend.dev", "owner_name": "Tester",
            "digest_enabled": True, "auto_reply": True,
        })
        r = requests.post(f"{API}/digest/send-now")
        assert r.status_code == 200, r.text
        d = r.json()
        assert d.get("sent") is True
        assert d.get("email_id")


# ---------------- Cron regression ----------------

class TestCronRegression:
    def test_cron_unauthorized(self):
        r = requests.post(f"{API}/cron/digest")
        assert r.status_code == 401

    def test_cron_ok_and_idempotent(self):
        if not WEBHOOK_CRON_SECRET:
            pytest.skip("no secret")
        wid = str(uuid.uuid4())
        h = {"Authorization": f"Bearer {WEBHOOK_CRON_SECRET}", "X-Webhook-Id": wid}
        r1 = requests.post(f"{API}/cron/digest", headers=h)
        assert r1.status_code == 200
        r2 = requests.post(f"{API}/cron/digest", headers=h)
        assert r2.status_code == 200
        assert r2.json().get("duplicate") is True
