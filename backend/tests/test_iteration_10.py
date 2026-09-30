"""Iteration 10 — runtime MOCK/LIVE mode toggle tests"""
import os
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://payment-verify-bot-2.preview.emergentagent.com").rstrip("/")
API = f"{BASE_URL}/api"


@pytest.fixture(scope="module")
def client():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    yield s
    # Teardown: reset to mock=true
    try:
        s.post(f"{API}/mode", json={"mock_mode": True}, timeout=10)
    except Exception:
        pass


# --- Regression smoke ---
def test_root(client):
    r = client.get(f"{API}/", timeout=10)
    assert r.status_code == 200
    j = r.json()
    assert j.get("service") == "PayVerify Bot"
    assert "mock_mode" in j


def test_settings(client):
    r = client.get(f"{API}/settings", timeout=10)
    assert r.status_code == 200


def test_transactions_list(client):
    r = client.get(f"{API}/transactions?limit=5", timeout=10)
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_transactions_stats(client):
    r = client.get(f"{API}/transactions/stats", timeout=10)
    assert r.status_code == 200
    assert "total" in r.json()


def test_gpay_accounts_list(client):
    r = client.get(f"{API}/gpay/accounts", timeout=10)
    assert r.status_code == 200


# --- New: /api/mode ---
def test_mode_initial_true(client):
    # ensure mock=true baseline
    client.post(f"{API}/mode", json={"mock_mode": True}, timeout=10)
    r = client.get(f"{API}/mode", timeout=10)
    assert r.status_code == 200
    assert r.json() == {"mock_mode": True}


def test_mode_flip_to_live(client):
    r = client.post(f"{API}/mode", json={"mock_mode": False}, timeout=10)
    assert r.status_code == 200
    assert r.json() == {"mock_mode": False}

    # /api/status reflects change
    st = client.get(f"{API}/status", timeout=10).json()
    assert st["mock_mode"] is False

    # /api/diagnostics.system.mock_mode reflects change (skip playwright — will report false OK)
    diag = client.get(f"{API}/diagnostics", timeout=60).json()
    assert diag["system"]["mock_mode"] is False


def test_mode_flip_back_to_mock(client):
    r = client.post(f"{API}/mode", json={"mock_mode": True}, timeout=10)
    assert r.status_code == 200
    assert r.json() == {"mock_mode": True}

    st = client.get(f"{API}/status", timeout=10).json()
    assert st["mock_mode"] is True

    diag = client.get(f"{API}/diagnostics", timeout=30).json()
    assert diag["system"]["mock_mode"] is True


def test_gpay_add_respects_mock_toggle(client):
    # Ensure mock=true
    client.post(f"{API}/mode", json={"mock_mode": True}, timeout=10)
    payload = {"label": "TEST_iter10", "email": "TEST_iter10@example.com", "password": "pw123"}
    r = client.post(f"{API}/gpay/accounts", json=payload, timeout=30)
    assert r.status_code == 200, r.text
    body = r.json()
    login = body.get("login", {})
    # Should be mock login when runtime mock_mode is true
    assert login.get("mock") is True, f"expected mock login, got {login}"
    acct_id = body.get("account", {}).get("id") or body.get("id")
    # Cleanup: delete the test account
    if acct_id:
        client.delete(f"{API}/gpay/accounts/{acct_id}", timeout=10)


def test_final_state_is_mock(client):
    r = client.get(f"{API}/mode", timeout=10).json()
    assert r["mock_mode"] is True
