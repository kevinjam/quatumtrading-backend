"""Iteration 15 — POST /api/candles/analyze with and without highlight.

Verifies the new analyze flow returns:
  signal in {BUY,SELL,HOLD}, confidence, trend,
  support_levels (>=1 with price/type/strength), resistance_levels (>=1),
  indicators (rsi_14, macd, bollinger), indicators_read (rsi, macd, bollinger).
"""
import os
import time
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://quant-trade-analysis.preview.emergentagent.com").rstrip("/")
SESSION_TOKEN = os.environ.get("ITER15_SESSION_TOKEN", "test_session_iter15_1782350284230")

HEADERS = {"Authorization": f"Bearer {SESSION_TOKEN}", "Content-Type": "application/json"}


@pytest.fixture(scope="module")
def candles_payload():
    """Pull live candles once to derive a highlight range."""
    r = requests.get(f"{BASE_URL}/api/candles", params={"symbol": "AAPL", "interval": "1d"}, headers=HEADERS, timeout=30)
    assert r.status_code == 200, f"GET /candles failed: {r.status_code} {r.text[:200]}"
    data = r.json()
    candles = data.get("candles") or []
    assert len(candles) > 30, f"Need at least 30 candles, got {len(candles)}"
    return candles


def _validate_analysis(body):
    assert body.get("signal") in {"BUY", "SELL", "HOLD"}, f"bad signal: {body.get('signal')}"
    assert body.get("confidence") in {"low", "medium", "high"}
    assert body.get("trend") in {"uptrend", "downtrend", "ranging"}

    sl = body.get("support_levels") or []
    rl = body.get("resistance_levels") or []
    assert len(sl) >= 1, "support_levels must have >=1 entry"
    assert len(rl) >= 1, "resistance_levels must have >=1 entry"
    for lvl in sl + rl:
        assert "price" in lvl
        assert lvl.get("type") in {"bullish", "bearish"}
        assert lvl.get("strength") in {"strong", "weak"}

    ind = body.get("indicators") or {}
    assert "rsi_14" in ind, "indicators.rsi_14 missing"
    assert "macd" in ind and isinstance(ind["macd"], dict)
    assert "bollinger" in ind and isinstance(ind["bollinger"], dict)
    assert "hist" in ind["macd"]
    assert "upper" in ind["bollinger"] and "lower" in ind["bollinger"]

    reads = body.get("indicators_read") or {}
    for k in ("rsi", "macd", "bollinger"):
        assert isinstance(reads.get(k), str) and len(reads[k]) > 0, f"indicators_read.{k} missing"


def test_analyze_without_highlight(candles_payload):
    body = {"symbol": "AAPL", "interval": "1d"}
    r = requests.post(f"{BASE_URL}/api/candles/analyze", json=body, headers=HEADERS, timeout=90)
    assert r.status_code == 200, f"analyze failed: {r.status_code} {r.text[:400]}"
    _validate_analysis(r.json())


def test_analyze_with_highlight(candles_payload):
    # Use last ~30 candles as the highlight range
    sub = candles_payload[-30:]
    start_t = int(sub[0]["time"])
    end_t = int(sub[-1]["time"])
    assert end_t > start_t

    body = {
        "symbol": "AAPL",
        "interval": "1d",
        "highlight": {"start_time": start_t, "end_time": end_t},
    }
    r = requests.post(f"{BASE_URL}/api/candles/analyze", json=body, headers=HEADERS, timeout=90)
    assert r.status_code == 200, f"analyze with highlight failed: {r.status_code} {r.text[:400]}"
    _validate_analysis(r.json())


def test_analyze_with_inverted_highlight_is_accepted(candles_payload):
    # end < start — backend should still respond, just won't slice
    sub = candles_payload[-30:]
    body = {
        "symbol": "AAPL",
        "interval": "1d",
        "highlight": {"start_time": int(sub[-1]["time"]), "end_time": int(sub[0]["time"])},
    }
    r = requests.post(f"{BASE_URL}/api/candles/analyze", json=body, headers=HEADERS, timeout=90)
    assert r.status_code == 200, f"analyze with inverted highlight failed: {r.status_code} {r.text[:300]}"
    _validate_analysis(r.json())
