"""Iteration_8: AI fallback for equity/shares when SEC EDGAR fails or lacks 2 quarters.

Pipeline order (analysis.py:54-78):
    SEC EDGAR (get_equity_and_shares) -> Claude AI (ai_estimate_equity_shares) -> manual stub (only if manual_price)

Each /api/analyze with AI fallback can take ~30-45s (LLM ~10-15s + SEC + Finnhub + LLM meta).
"""
import os
import pytest
import requests

BASE_URL = os.environ.get(
    "REACT_APP_BACKEND_URL",
    "https://quant-trade-analysis.preview.emergentagent.com",
).rstrip("/")
SESSION_TOKEN = os.environ.get("TEST_SESSION_TOKEN", "test_session_people_1781748718633")

ANALYZE_TIMEOUT = 120  # AI estimate + SEC + LLM meta can be slow


@pytest.fixture(scope="session")
def auth_headers():
    assert SESSION_TOKEN, "TEST_SESSION_TOKEN required"
    return {"Authorization": f"Bearer {SESSION_TOKEN}", "Content-Type": "application/json"}


# ---------- Primary: AI fallback for foreign ADR (ASML) ----------
def test_analyze_asml_triggers_ai_fallback(auth_headers):
    """ASML files 20-F (not 10-Q) so SEC EDGAR's 10-Q lookup typically fails → AI fallback."""
    r = requests.post(
        f"{BASE_URL}/api/analyze",
        headers=auth_headers,
        json={"ticker": "ASML"},
        timeout=ANALYZE_TIMEOUT,
    )
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text[:600]}"
    data = r.json()

    # data_sources must reference AI estimate
    ds = (data.get("data_sources") or "")
    assert "AI estimate" in ds, f"data_sources missing 'AI estimate': {ds}"

    # v2 quarters must be populated (not N/A / null / empty)
    qc = data["v2"]["quarter_current"]
    qp = data["v2"]["quarter_prior"]
    assert qc and qc not in ("N/A", ""), f"v2.quarter_current empty/N-A: {qc!r}"
    assert qp and qp not in ("N/A", ""), f"v2.quarter_prior empty/N-A: {qp!r}"

    # equity_current_millions must be > 0
    eq_curr_mm = data["v2"]["equity_current_millions"]
    assert eq_curr_mm > 0, f"v2.equity_current_millions {eq_curr_mm} not > 0"

    # shares should be > 0 (sanity)
    assert data["v2"]["shares_current_millions"] > 0, f"shares 0: {data['v2']}"

    # final_signal must be present
    assert data.get("final_signal") is not None, "final_signal missing"


# ---------- Regression: AAPL still uses SEC EDGAR (no AI) ----------
def test_analyze_aapl_uses_sec_edgar(auth_headers):
    r = requests.post(
        f"{BASE_URL}/api/analyze",
        headers=auth_headers,
        json={"ticker": "AAPL"},
        timeout=ANALYZE_TIMEOUT,
    )
    assert r.status_code == 200, r.text[:500]
    data = r.json()
    ds = (data.get("data_sources") or "")
    assert "SEC EDGAR: 10-Q filed" in ds, f"AAPL not using SEC EDGAR 10-Q: {ds}"
    assert "AI estimate" not in ds, f"AAPL incorrectly routed to AI: {ds}"
    # v2 quarters populated
    assert data["v2"]["quarter_current"] not in (None, "", "N/A")


# ---------- Regression: NVDA still uses SEC EDGAR ----------
def test_analyze_nvda_uses_sec_edgar(auth_headers):
    r = requests.post(
        f"{BASE_URL}/api/analyze",
        headers=auth_headers,
        json={"ticker": "NVDA"},
        timeout=ANALYZE_TIMEOUT,
    )
    assert r.status_code == 200, r.text[:500]
    data = r.json()
    ds = (data.get("data_sources") or "")
    assert "SEC EDGAR: 10-Q filed" in ds, f"NVDA not using SEC EDGAR 10-Q: {ds}"
    assert "AI estimate" not in ds, f"NVDA incorrectly routed to AI: {ds}"


# ---------- ZZZZX without manual_price → 422 ----------
def test_analyze_zzzzx_without_manual_price_returns_422(auth_headers):
    """ZZZZX has no price + no SEC + AI can't estimate fictitious ticker → 422.
    Note: for an unknown ticker, price fetch (get_price_data) fails *before* SEC/AI,
    so the detail may be 'Incomplete price data' rather than 'Equity/shares data unavailable'.
    Either is acceptable: the contract is just 'no analyze without manual override'.
    """
    r = requests.post(
        f"{BASE_URL}/api/analyze",
        headers=auth_headers,
        json={"ticker": "ZZZZX"},
        timeout=ANALYZE_TIMEOUT,
    )
    assert r.status_code == 422, f"Expected 422, got {r.status_code}: {r.text[:300]}"
    detail = (r.json().get("detail") or "").lower()
    # Accept either failure mode (price-first or equity-second).
    assert any(
        kw in detail
        for kw in ("equity/shares data unavailable", "incomplete price data", "edgar", "2 quarters")
    ), f"Unexpected 422 detail: {detail!r}"


# ---------- ZZZZX with manual_price → 200 (stub fallback still works) ----------
def test_analyze_zzzzx_with_manual_price_stub_fallback(auth_headers):
    payload = {
        "ticker": "ZZZZX",
        "manual_price": {"high_52w": 100, "low_52w": 50, "current": 70},
    }
    r = requests.post(
        f"{BASE_URL}/api/analyze",
        headers=auth_headers,
        json=payload,
        timeout=ANALYZE_TIMEOUT,
    )
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text[:600]}"
    data = r.json()
    assert data.get("final_signal") is not None
    # V1 = (100-50)/70 *100 ≈ 71.43
    assert abs(data["v1"]["value"] - 71.43) < 0.1
    ds = (data.get("data_sources") or "").lower()
    assert "manual entry" in ds, f"manual entry missing: {ds}"
