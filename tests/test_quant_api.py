"""Backend API tests for quant trading analysis app."""
import os
import time
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://quant-trade-analysis.preview.emergentagent.com").rstrip("/")
SESSION_TOKEN = os.environ.get("TEST_SESSION_TOKEN")


@pytest.fixture(scope="session")
def auth_headers():
    assert SESSION_TOKEN, "TEST_SESSION_TOKEN must be set"
    return {"Authorization": f"Bearer {SESSION_TOKEN}", "Content-Type": "application/json"}


@pytest.fixture(scope="session")
def analyses_state():
    """Shared dict to carry analysis_ids between tests."""
    return {}


# ----------------------- HEALTH -----------------------
def test_health_root():
    r = requests.get(f"{BASE_URL}/api/", timeout=15)
    assert r.status_code == 200
    data = r.json()
    assert data.get("service") == "quant-trading-analysis"
    assert data.get("ok") is True


# ----------------------- AUTH -----------------------
def test_auth_me_unauthenticated():
    r = requests.get(f"{BASE_URL}/api/auth/me", timeout=15)
    assert r.status_code == 401


def test_auth_me_authenticated(auth_headers):
    r = requests.get(f"{BASE_URL}/api/auth/me", headers=auth_headers, timeout=15)
    assert r.status_code == 200, r.text
    data = r.json()
    assert "user_id" in data
    assert "email" in data
    assert data["email"].startswith("test.user.")


# ----------------------- ANALYZE -----------------------
def test_analyze_invalid_ticker(auth_headers):
    r = requests.post(
        f"{BASE_URL}/api/analyze",
        headers=auth_headers,
        json={"ticker": "ZZZZZZ"},
        timeout=120,
    )
    assert r.status_code == 422, f"Expected 422, got {r.status_code}: {r.text[:300]}"
    data = r.json()
    assert "detail" in data


def test_analyze_nvda(auth_headers, analyses_state):
    r = requests.post(
        f"{BASE_URL}/api/analyze",
        headers=auth_headers,
        json={"ticker": "NVDA"},
        timeout=180,
    )
    assert r.status_code == 200, f"Got {r.status_code}: {r.text[:500]}"
    data = r.json()
    # Required top-level keys
    for k in [
        "analysis_id", "ticker", "company", "price_data", "v1", "v2", "v3", "v4",
        "v5", "v6", "going_concern", "going_concern_note", "final_signal",
        "final_reasoning", "data_sources",
    ]:
        assert k in data, f"missing key {k}"

    assert data["ticker"] == "NVDA"
    # price_data shape
    pd = data["price_data"]
    for k in ["high_52w", "low_52w", "current", "position_pct"]:
        assert k in pd
    # v1 verification: (high - low)/current * 100
    expected_v1 = (pd["high_52w"] - pd["low_52w"]) / pd["current"] * 100
    assert abs(data["v1"]["value"] - round(expected_v1, 2)) < 0.5
    for k in ["value", "signal", "position_pct"]:
        assert k in data["v1"]
    # v2 keys
    v2_keys = ["value", "direction", "override_fires", "distortion_type", "distortion_note",
               "bv_current", "bv_prior", "equity_current_millions", "equity_prior_millions",
               "shares_current_millions", "shares_prior_millions", "quarter_current", "quarter_prior"]
    for k in v2_keys:
        assert k in data["v2"], f"v2 missing {k}"
    # v3
    for k in ["score", "signal", "direction", "override_active", "invalid", "invalid_reason"]:
        assert k in data["v3"]
    # v4
    for k in ["v4a_ratio", "v4b_ratio", "v4a_signal", "v4b_signal", "overall", "notes"]:
        assert k in data["v4"]
    # v5
    for k in ["rating", "signal", "reasoning"]:
        assert k in data["v5"]
    # v6
    for k in ["applicable", "decision", "earnings_date", "criteria_met", "reasoning"]:
        assert k in data["v6"]

    analyses_state["nvda_id"] = data["analysis_id"]
    analyses_state["nvda_full"] = data


def test_analyze_aapl(auth_headers, analyses_state):
    r = requests.post(
        f"{BASE_URL}/api/analyze",
        headers=auth_headers,
        json={"ticker": "AAPL"},
        timeout=180,
    )
    assert r.status_code == 200, r.text[:500]
    data = r.json()
    assert data["ticker"] == "AAPL"
    pd = data["price_data"]
    expected_v1 = (pd["high_52w"] - pd["low_52w"]) / pd["current"] * 100
    assert abs(data["v1"]["value"] - round(expected_v1, 2)) < 0.5
    analyses_state["aapl_id"] = data["analysis_id"]


# ----------------------- HISTORY -----------------------
def test_history_list(auth_headers, analyses_state):
    # small delay to ensure insert is durable
    time.sleep(1)
    r = requests.get(f"{BASE_URL}/api/history", headers=auth_headers, timeout=30)
    assert r.status_code == 200
    rows = r.json()
    assert isinstance(rows, list)
    assert len(rows) >= 2, f"Expected >=2 rows, got {len(rows)}"
    for row in rows[:5]:
        for k in ["analysis_id", "ticker", "final_signal", "company", "created_at"]:
            assert k in row, f"history row missing {k}"


def test_history_detail(auth_headers, analyses_state):
    aid = analyses_state.get("nvda_id")
    assert aid, "no nvda_id from prior test"
    r = requests.get(f"{BASE_URL}/api/history/{aid}", headers=auth_headers, timeout=30)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["analysis_id"] == aid
    assert data["ticker"] == "NVDA"
    assert "v1" in data and "v2" in data and "v3" in data


# ----------------------- LOGOUT -----------------------
def test_logout_and_me_unauth():
    """Use a fresh test session for logout to avoid breaking other tests."""
    # Create new session via mongo (skip if mongosh not available)
    import subprocess, json
    try:
        token = "test_logout_" + str(int(time.time()))
        uid = "test-logout-" + str(int(time.time()))
        cmd = f"""mongosh --quiet --eval "
use('test_database');
db.users.insertOne({{user_id:'{uid}',email:'{uid}@example.com',name:'Logout Test',picture:'',created_at:new Date().toISOString()}});
db.user_sessions.insertOne({{user_id:'{uid}',session_token:'{token}',expires_at:new Date(Date.now()+7*86400000).toISOString(),created_at:new Date().toISOString()}});
"
        """
        subprocess.run(cmd, shell=True, check=True, capture_output=True)
    except Exception as e:
        pytest.skip(f"Cannot create test session: {e}")

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    # verify me works
    r = requests.get(f"{BASE_URL}/api/auth/me", headers=headers, timeout=15)
    assert r.status_code == 200, r.text

    # logout uses cookie — set via cookies dict
    with requests.Session() as s:
        s.cookies.set("session_token", token)
        r = s.post(f"{BASE_URL}/api/auth/logout", timeout=15)
        assert r.status_code == 200
        # cookie cleared, session removed in DB
        r2 = s.get(f"{BASE_URL}/api/auth/me", timeout=15)
        assert r2.status_code == 401
