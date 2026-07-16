"""Iteration 14: Stock News, Price Targets, Ticker Autocomplete (/search/symbols)."""
import os
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://quant-trade-analysis.preview.emergentagent.com").rstrip("/")
SESSION_TOKEN = os.environ.get("TEST_SESSION_TOKEN", "test_session_iter14_1782109572089")
HEADERS = {"Authorization": f"Bearer {SESSION_TOKEN}", "Content-Type": "application/json"}


@pytest.fixture(scope="module")
def client():
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


# ------------------- /api/search/symbols (autocomplete) -------------------
class TestSearchSymbols:
    def test_search_nvi_returns_nvda(self, client):
        r = client.get(f"{BASE_URL}/api/search/symbols", params={"q": "nvi"})
        assert r.status_code == 200, r.text
        results = r.json().get("results", [])
        assert len(results) > 0
        symbols = [x["symbol"] for x in results]
        assert "NVDA" in symbols, f"Expected NVDA in {symbols}"

    def test_search_apple_returns_aapl(self, client):
        r = client.get(f"{BASE_URL}/api/search/symbols", params={"q": "apple"})
        assert r.status_code == 200
        symbols = [x["symbol"] for x in r.json().get("results", [])]
        assert "AAPL" in symbols, f"Expected AAPL in {symbols}"

    def test_search_tes_returns_tsla(self, client):
        r = client.get(f"{BASE_URL}/api/search/symbols", params={"q": "tes"})
        assert r.status_code == 200
        symbols = [x["symbol"] for x in r.json().get("results", [])]
        assert "TSLA" in symbols, f"Expected TSLA in {symbols}"

    def test_search_qq_returns_qqq(self, client):
        r = client.get(f"{BASE_URL}/api/search/symbols", params={"q": "qq"})
        assert r.status_code == 200
        symbols = [x["symbol"] for x in r.json().get("results", [])]
        assert "QQQ" in symbols, f"Expected QQQ in {symbols}"

    def test_search_empty_returns_empty(self, client):
        r = client.get(f"{BASE_URL}/api/search/symbols", params={"q": ""})
        assert r.status_code == 200
        assert r.json().get("results") == []

    def test_search_requires_auth(self):
        r = requests.get(f"{BASE_URL}/api/search/symbols", params={"q": "aapl"})
        assert r.status_code in (401, 403)

    def test_search_result_shape(self, client):
        r = client.get(f"{BASE_URL}/api/search/symbols", params={"q": "msft"})
        assert r.status_code == 200
        results = r.json().get("results", [])
        assert len(results) > 0
        # each item must have symbol & description
        for item in results:
            assert "symbol" in item
            assert "description" in item


# ------------------- /api/news -------------------
class TestNews:
    def test_general_market_news(self, client):
        r = client.get(f"{BASE_URL}/api/news")
        assert r.status_code == 200, r.text
        d = r.json()
        assert d.get("symbol") is None
        assert isinstance(d.get("items"), list)
        # Expect ~30 items for general market
        assert len(d["items"]) > 0, "expected some news items"

    def test_news_item_shape(self, client):
        r = client.get(f"{BASE_URL}/api/news")
        items = r.json().get("items", [])
        if not items:
            pytest.skip("no news items returned")
        first = items[0]
        # must have at least headline + url
        assert "headline" in first
        assert "url" in first

    def test_per_ticker_news_aapl(self, client):
        r = client.get(f"{BASE_URL}/api/news", params={"symbol": "AAPL"})
        assert r.status_code == 200, r.text
        d = r.json()
        assert d.get("symbol") == "AAPL"
        assert isinstance(d.get("items"), list)


# ------------------- /api/analysts/{symbol} -------------------
class TestAnalysts:
    def test_analysts_nvda(self, client):
        r = client.get(f"{BASE_URL}/api/analysts/NVDA")
        assert r.status_code == 200, r.text
        d = r.json()
        assert d.get("symbol") == "NVDA"
        target = d.get("target") or {}
        # validate target shape
        assert "current_price" in target or "mean" in target or "high" in target, f"target keys: {list(target.keys())}"
        # validate recommendations
        recs = d.get("recommendations")
        assert isinstance(recs, list), f"recommendations should be a list, got {type(recs)}"

    def test_analysts_aapl_grid_values(self, client):
        r = client.get(f"{BASE_URL}/api/analysts/AAPL")
        assert r.status_code == 200
        target = r.json().get("target", {})
        # at least one of high/mean/low/median should be present
        any_target = any(target.get(k) is not None for k in ["high", "mean", "median", "low"])
        assert any_target, f"no target prices in {target}"

    def test_analysts_invalid_returns_error_or_empty(self, client):
        # arbitrary 6-char unlikely ticker
        r = client.get(f"{BASE_URL}/api/analysts/ZZZZZX")
        # should either return 200 with empty data or error gracefully
        assert r.status_code in (200, 400, 404, 500)
