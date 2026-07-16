"""Iteration_7: manual_price + SEC EDGAR failure fallback (stub SEC dict)."""
import os
import pytest
import requests

BASE_URL = os.environ.get(
    "REACT_APP_BACKEND_URL",
    "https://quant-trade-analysis.preview.emergentagent.com",
).rstrip("/")
SESSION_TOKEN = os.environ.get("TEST_SESSION_TOKEN", "test_session_people_1781748718633")


@pytest.fixture(scope="session")
def auth_headers():
    assert SESSION_TOKEN, "TEST_SESSION_TOKEN required"
    return {"Authorization": f"Bearer {SESSION_TOKEN}", "Content-Type": "application/json"}


# ---- Primary: manual_price + SEC failure fallback (the fix) ----
def test_analyze_zzzzx_with_manual_price_stub_sec(auth_headers):
    """ZZZZX has no SEC EDGAR coverage; with manual_price, V1 must still compute."""
    payload = {
        "ticker": "ZZZZX",
        "manual_price": {"high_52w": 100, "low_52w": 50, "current": 70},
    }
    r = requests.post(
        f"{BASE_URL}/api/analyze",
        headers=auth_headers,
        json=payload,
        timeout=180,
    )
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text[:600]}"
    data = r.json()

    # final_signal should be present (not null)
    assert data.get("final_signal") is not None, f"final_signal null: {data}"

    # V1 — (100-50)/70 * 100 = 71.4286
    expected_v1 = (100 - 50) / 70 * 100
    assert abs(data["v1"]["value"] - round(expected_v1, 2)) < 0.1, \
        f"v1.value {data['v1']['value']} != ~{round(expected_v1, 2)}"
    assert data["v1"]["signal"] == "Bear", f"v1.signal {data['v1']['signal']} != Bear"

    # V2 should be 0 because stub SEC has equity=0, shares=1 → bv=0, bv_prior=0
    assert data["v2"]["value"] == 0, f"v2.value {data['v2']['value']} != 0"

    # data_sources string
    ds_lower = (data.get("data_sources") or "").lower()
    assert "no sec edgar coverage" in ds_lower, f"data_sources missing 'No SEC EDGAR coverage': {data.get('data_sources')}"
    assert "manual entry" in ds_lower, f"data_sources missing 'manual entry': {data.get('data_sources')}"


# ---- Regression: AAPL + manual_price (real SEC + manual price) ----
def test_analyze_aapl_with_manual_price_regression(auth_headers):
    payload = {
        "ticker": "AAPL",
        "manual_price": {"high_52w": 260, "low_52w": 160, "current": 210},
    }
    r = requests.post(
        f"{BASE_URL}/api/analyze",
        headers=auth_headers,
        json=payload,
        timeout=180,
    )
    assert r.status_code == 200, r.text[:500]
    data = r.json()
    pd_ = data["price_data"]
    assert pd_["high_52w"] == 260
    assert pd_["low_52w"] == 160
    assert pd_["current"] == 210
    expected_v1 = (260 - 160) / 210 * 100  # 47.62
    assert abs(data["v1"]["value"] - round(expected_v1, 2)) < 0.1
    assert "manual entry" in data["data_sources"].lower()
    # Real SEC coverage → quarter values should be populated (not 'N/A')
    assert data["v2"]["quarter_current"] not in (None, "N/A", ""), \
        f"v2.quarter_current should be populated for AAPL: {data['v2']}"


# ---- Regression: AAPL pure (no manual_price) ----
def test_analyze_aapl_without_manual_price_regression(auth_headers):
    r = requests.post(
        f"{BASE_URL}/api/analyze",
        headers=auth_headers,
        json={"ticker": "AAPL"},
        timeout=180,
    )
    assert r.status_code == 200, r.text[:500]
    data = r.json()
    assert data["ticker"] == "AAPL"
    assert data.get("final_signal") is not None
    # Should have real SEC data (not stub)
    ds_lower = (data.get("data_sources") or "").lower()
    assert "no sec edgar coverage" not in ds_lower, "Should not be stub for AAPL"
