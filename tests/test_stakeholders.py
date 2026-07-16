"""Tests for the new stakeholders feature (trillion-$ firms + 100B+ billionaires)."""
import os
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")
SESSION_TOKEN = os.environ.get("TEST_SESSION_TOKEN")


@pytest.fixture(scope="session")
def auth_headers():
    assert SESSION_TOKEN, "TEST_SESSION_TOKEN must be set"
    return {"Authorization": f"Bearer {SESSION_TOKEN}", "Content-Type": "application/json"}


@pytest.fixture(scope="session")
def state():
    return {}


def _analyze(headers, ticker):
    r = requests.post(
        f"{BASE_URL}/api/analyze",
        headers=headers,
        json={"ticker": ticker},
        timeout=240,
    )
    assert r.status_code == 200, f"{ticker} -> {r.status_code}: {r.text[:500]}"
    return r.json()


# --------- shape ---------
def test_aapl_stakeholders_shape_and_trillion(auth_headers, state):
    data = _analyze(auth_headers, "AAPL")
    state["aapl"] = data
    assert "stakeholders" in data, "stakeholders missing from response"
    s = data["stakeholders"]
    assert "trillion_dollar_firms" in s
    assert "billionaires" in s
    assert isinstance(s["trillion_dollar_firms"], list)
    assert isinstance(s["billionaires"], list)

    # AAPL should have many big institutional holders
    firms = s["trillion_dollar_firms"]
    assert len(firms) >= 5, f"Expected >=5 trillion-$ firms for AAPL, got {len(firms)}: {[f['holder'] for f in firms]}"
    names = " ".join(f["holder"].lower() for f in firms)
    # Vanguard + BlackRock + State Street are guaranteed for AAPL
    assert "vanguard" in names, f"Vanguard missing from AAPL firms: {names}"
    assert "blackrock" in names, f"BlackRock missing from AAPL firms: {names}"
    assert "state street" in names, f"State Street missing: {names}"

    # Each firm shape
    for f in firms:
        assert {"holder", "pct_held", "value_usd", "shares"} <= set(f.keys())
        assert isinstance(f["pct_held"], (int, float))
        assert f["pct_held"] > 0


def test_aapl_billionaire_buffett(auth_headers, state):
    data = state.get("aapl") or _analyze(auth_headers, "AAPL")
    state["aapl"] = data
    b = data["stakeholders"]["billionaires"]
    assert isinstance(b, list)
    names = [x.get("name", "").lower() for x in b]
    assert any("buffett" in n for n in names), f"Warren Buffett missing from AAPL billionaires: {names}"
    for x in b:
        assert {"name", "role", "net_worth_b"} <= set(x.keys())
        assert isinstance(x["net_worth_b"], (int, float))
        assert x["net_worth_b"] >= 100, f"{x['name']} has net_worth_b<100: {x['net_worth_b']}"


# --------- NVDA ---------
def test_nvda_jensen_huang(auth_headers, state):
    data = _analyze(auth_headers, "NVDA")
    state["nvda"] = data
    b = data["stakeholders"]["billionaires"]
    names = [x.get("name", "").lower() for x in b]
    assert any("jensen" in n or "huang" in n for n in names), f"Jensen Huang missing from NVDA: {names}"
    huang = next(x for x in b if "huang" in x["name"].lower())
    assert huang["net_worth_b"] >= 100, f"Huang net worth <100B: {huang}"

    # NVDA should also have trillion-$ institutional firms
    firms = data["stakeholders"]["trillion_dollar_firms"]
    assert len(firms) >= 3, f"Expected >=3 trillion-$ firms for NVDA, got {len(firms)}"


# --------- TSLA ---------
def test_tsla_elon_musk(auth_headers, state):
    data = _analyze(auth_headers, "TSLA")
    state["tsla"] = data
    b = data["stakeholders"]["billionaires"]
    names = [x.get("name", "").lower() for x in b]
    assert any("musk" in n for n in names), f"Elon Musk missing from TSLA billionaires: {names}"
    musk = next(x for x in b if "musk" in x["name"].lower())
    assert musk["net_worth_b"] >= 100


# --------- Regression: V1-V6 still intact ---------
def test_v1_v6_regression_aapl(auth_headers, state):
    data = state.get("aapl") or _analyze(auth_headers, "AAPL")
    for k in ["v1", "v2", "v3", "v4", "v5", "v6",
              "price_data", "final_signal", "data_sources",
              "going_concern", "going_concern_note", "ticker", "company"]:
        assert k in data, f"regression: missing {k}"
    # v1.signal still present
    assert "signal" in data["v1"]
    assert "score" in data["v3"]
    assert "overall" in data["v4"]


# --------- History detail preserves stakeholders ---------
def test_history_detail_has_stakeholders(auth_headers, state):
    aid = state["nvda"]["analysis_id"]
    r = requests.get(f"{BASE_URL}/api/history/{aid}", headers=auth_headers, timeout=30)
    assert r.status_code == 200, r.text
    detail = r.json()
    assert "stakeholders" in detail, "history detail dropped stakeholders"
    s = detail["stakeholders"]
    assert "trillion_dollar_firms" in s
    assert "billionaires" in s
    # billionaire list should still contain Huang for NVDA
    names = [x.get("name", "").lower() for x in s["billionaires"]]
    assert any("huang" in n for n in names), f"NVDA history detail lost Huang: {names}"
