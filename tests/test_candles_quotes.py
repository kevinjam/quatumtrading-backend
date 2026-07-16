"""Tests for the new /api/quotes and /api/candles endpoints."""
import os
import pytest
import requests

BASE_URL = os.environ["REACT_APP_BACKEND_URL"].rstrip("/")
SESSION_TOKEN = os.environ.get("TEST_SESSION_TOKEN")

DEFAULT_WATCHLIST = ["QQQ", "SPY", "NVDA", "AAPL", "MSFT", "TSLA", "AMZN", "GOOGL"]


@pytest.fixture(scope="session")
def auth_headers():
    assert SESSION_TOKEN, "TEST_SESSION_TOKEN must be set"
    return {"Authorization": f"Bearer {SESSION_TOKEN}", "Content-Type": "application/json"}


# --------------------- QUOTES ---------------------
def test_quotes_unauthenticated():
    r = requests.get(f"{BASE_URL}/api/quotes", timeout=30)
    assert r.status_code == 401


def test_quotes_default_watchlist(auth_headers):
    r = requests.get(f"{BASE_URL}/api/quotes", headers=auth_headers, timeout=60)
    assert r.status_code == 200, r.text[:500]
    data = r.json()
    assert "quotes" in data
    quotes = data["quotes"]
    assert len(quotes) == 8, f"Expected 8 quotes, got {len(quotes)}"
    symbols = [q["symbol"] for q in quotes]
    assert symbols == DEFAULT_WATCHLIST, f"Default watchlist order mismatch: {symbols}"
    # at least one quote must have price field (free tier should provide US equities)
    priced = [q for q in quotes if q.get("price") is not None]
    assert len(priced) >= 1, f"No quotes contained price field: {quotes}"
    for q in priced[:3]:
        for k in ["price", "change", "percent", "high", "low", "open", "prev_close"]:
            assert k in q, f"missing key {k} in {q}"


def test_quotes_custom_symbols(auth_headers):
    r = requests.get(
        f"{BASE_URL}/api/quotes",
        params={"symbols": "AAPL,NVDA"},
        headers=auth_headers,
        timeout=60,
    )
    assert r.status_code == 200, r.text[:300]
    data = r.json()
    quotes = data["quotes"]
    assert len(quotes) == 2
    assert {q["symbol"] for q in quotes} == {"AAPL", "NVDA"}


# --------------------- CANDLES ---------------------
@pytest.mark.parametrize("interval", ["5m", "1h", "1d", "1w"])
def test_candles_intervals(auth_headers, interval):
    r = requests.get(
        f"{BASE_URL}/api/candles",
        params={"symbol": "NVDA", "interval": interval},
        headers=auth_headers,
        timeout=60,
    )
    assert r.status_code == 200, f"{interval} failed: {r.text[:300]}"
    data = r.json()
    assert data["symbol"] == "NVDA"
    assert data["interval"] == interval
    assert "candles" in data
    candles = data["candles"]
    assert isinstance(candles, list)
    assert len(candles) >= 50, f"{interval}: expected >=50 candles, got {len(candles)}"
    # validate first/last candle shape
    for c in [candles[0], candles[-1]]:
        for k in ["time", "open", "high", "low", "close", "volume"]:
            assert k in c, f"candle missing {k}"
        assert isinstance(c["time"], int)
        assert c["high"] >= c["low"]


def test_candles_invalid_interval(auth_headers):
    r = requests.get(
        f"{BASE_URL}/api/candles",
        params={"symbol": "NVDA", "interval": "2h"},
        headers=auth_headers,
        timeout=30,
    )
    assert r.status_code == 400, f"Expected 400, got {r.status_code}: {r.text[:200]}"


def test_candles_unauthenticated():
    r = requests.get(
        f"{BASE_URL}/api/candles",
        params={"symbol": "NVDA", "interval": "1d"},
        timeout=30,
    )
    assert r.status_code == 401


# --------------------- CANDLE ANALYZE ---------------------
def test_candles_analyze_unauthenticated():
    r = requests.post(
        f"{BASE_URL}/api/candles/analyze",
        json={"symbol": "NVDA", "interval": "1d"},
        timeout=30,
    )
    assert r.status_code == 401


def test_candles_analyze_nvda(auth_headers):
    r = requests.post(
        f"{BASE_URL}/api/candles/analyze",
        headers=auth_headers,
        json={"symbol": "NVDA", "interval": "1d"},
        timeout=90,
    )
    assert r.status_code == 200, f"Got {r.status_code}: {r.text[:500]}"
    data = r.json()
    required = [
        "symbol", "interval", "trend", "patterns", "support", "resistance",
        "momentum", "signal", "entry", "stop_loss", "take_profit_1",
        "take_profit_2", "confidence", "reasoning",
    ]
    for k in required:
        assert k in data, f"missing key {k}: {list(data.keys())}"
    assert data["signal"] in ("BUY", "SELL", "HOLD"), f"bad signal {data['signal']}"
    assert data["trend"] in ("uptrend", "downtrend", "ranging"), f"bad trend {data['trend']}"
    assert data["confidence"] in ("low", "medium", "high")
    assert isinstance(data["patterns"], list)
    for k in ["support", "resistance", "entry", "stop_loss", "take_profit_1", "take_profit_2"]:
        assert isinstance(data[k], (int, float)), f"{k} not numeric: {data[k]}"
    assert len(data["reasoning"]) > 20
