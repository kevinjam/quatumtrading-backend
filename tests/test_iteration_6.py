"""Tests for iteration_6: manual_price, Corp holdings, Caution rename."""
import os
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://quant-trade-analysis.preview.emergentagent.com").rstrip("/")
SESSION_TOKEN = os.environ.get("TEST_SESSION_TOKEN")


@pytest.fixture(scope="session")
def auth_headers():
    assert SESSION_TOKEN, "TEST_SESSION_TOKEN required"
    return {"Authorization": f"Bearer {SESSION_TOKEN}", "Content-Type": "application/json"}


# ---------------- People: Corp branch ----------------
def test_people_holdings_nvidia_corp(auth_headers):
    r = requests.post(
        f"{BASE_URL}/api/people/holdings",
        headers=auth_headers,
        json={"name": "NVIDIA", "role": "Corp"},
        timeout=60,
    )
    assert r.status_code == 200, r.text[:400]
    data = r.json()
    assert "holdings" in data
    tickers = [h.get("ticker", "").upper() for h in data["holdings"]]
    companies = " ".join((h.get("company") or "") for h in data["holdings"]).lower()
    # Must NOT include NVDA itself
    assert "NVDA" not in tickers, f"NVDA returned in own corp holdings: {tickers}"
    # Must include at least one of known holdings
    expected_any = ["RXRX", "SOUN", "NNOX", "ARM"]
    expected_companies = ["recursion", "soundhound", "nano-x", "arm", "inflection", "cohere"]
    matched = any(t in tickers for t in expected_any) or any(c in companies for c in expected_companies)
    assert matched, f"None of {expected_any} or {expected_companies} found. tickers={tickers} companies={companies[:200]}"


def test_people_holdings_berkshire_corp(auth_headers):
    r = requests.post(
        f"{BASE_URL}/api/people/holdings",
        headers=auth_headers,
        json={"name": "Berkshire Hathaway", "role": "Corp"},
        timeout=60,
    )
    assert r.status_code == 200, r.text[:400]
    data = r.json()
    tickers = [h.get("ticker", "").upper() for h in data["holdings"]]
    expected_any = ["AAPL", "KO", "AXP", "BAC", "OXY", "CVX"]
    matched = any(t in tickers for t in expected_any)
    assert matched, f"None of {expected_any} found. tickers={tickers}"


def test_people_holdings_trump_president_regression(auth_headers):
    r = requests.post(
        f"{BASE_URL}/api/people/holdings",
        headers=auth_headers,
        json={"name": "Donald Trump", "role": "President"},
        timeout=60,
    )
    assert r.status_code == 200, r.text[:400]
    data = r.json()
    tickers = [h.get("ticker", "").upper() for h in data["holdings"]]
    assert "DJT" in tickers, f"DJT not in Trump holdings: {tickers}"


# ---------------- Analyze: manual_price ----------------
def test_analyze_with_manual_price(auth_headers):
    payload = {
        "ticker": "AAPL",
        "manual_price": {"high_52w": 260, "low_52w": 160, "current": 210},
    }
    r = requests.post(f"{BASE_URL}/api/analyze", headers=auth_headers, json=payload, timeout=180)
    assert r.status_code == 200, r.text[:500]
    data = r.json()
    pd_ = data["price_data"]
    assert pd_["high_52w"] == 260
    assert pd_["low_52w"] == 160
    assert pd_["current"] == 210
    expected_v1 = (260 - 160) / 210 * 100  # 47.62
    assert abs(data["v1"]["value"] - round(expected_v1, 2)) < 0.1
    assert "manual entry" in data["data_sources"].lower()
    assert data["final_signal"] != "Do Not Enter"


def test_analyze_without_manual_price_regression(auth_headers):
    r = requests.post(
        f"{BASE_URL}/api/analyze",
        headers=auth_headers,
        json={"ticker": "AAPL"},
        timeout=180,
    )
    assert r.status_code == 200, r.text[:500]
    data = r.json()
    assert data["ticker"] == "AAPL"
    assert data["final_signal"] != "Do Not Enter"


def test_analyze_invalid_ticker_returns_422(auth_headers):
    r = requests.post(
        f"{BASE_URL}/api/analyze",
        headers=auth_headers,
        json={"ticker": "ZZZZX"},
        timeout=120,
    )
    assert r.status_code == 422, f"Got {r.status_code}: {r.text[:300]}"
    detail = r.json().get("detail", "")
    assert ("Incomplete price data" in detail) or ("not found in SEC EDGAR" in detail) or ("EDGAR" in detail), \
        f"Unexpected detail: {detail}"
