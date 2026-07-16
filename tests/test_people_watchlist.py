"""Tests for /api/people/holdings and /api/watchlist endpoints (iteration 4)."""
import os
import pytest
import requests

BASE_URL = os.environ["REACT_APP_BACKEND_URL"].rstrip("/")
SESSION_TOKEN = os.environ.get("TEST_SESSION_TOKEN", "test_session_people_1781748718633")


@pytest.fixture(scope="module")
def client():
    s = requests.Session()
    s.headers.update({
        "Content-Type": "application/json",
        "Authorization": f"Bearer {SESSION_TOKEN}",
    })
    return s


@pytest.fixture(scope="module", autouse=True)
def cleanup(client):
    # ensure clean state
    for t in ["DJT", "TSLA", "NVDA", "AAPL"]:
        client.delete(f"{BASE_URL}/api/watchlist/{t}")
    yield
    for t in ["DJT", "TSLA", "NVDA", "AAPL"]:
        client.delete(f"{BASE_URL}/api/watchlist/{t}")


# ============ people/holdings ============
class TestPeopleHoldings:
    def test_trump_president_contains_djt(self, client):
        r = client.post(f"{BASE_URL}/api/people/holdings",
                        json={"name": "Donald Trump", "role": "President"}, timeout=60)
        assert r.status_code == 200, r.text
        data = r.json()
        assert "person" in data and "holdings" in data
        assert isinstance(data["holdings"], list) and len(data["holdings"]) >= 1
        tickers = [h.get("ticker", "").upper() for h in data["holdings"]]
        assert "DJT" in tickers, f"Expected DJT in Trump's holdings; got {tickers}"

    def test_musk_ceo_contains_tsla(self, client):
        r = client.post(f"{BASE_URL}/api/people/holdings",
                        json={"name": "Elon Musk", "role": "CEO"}, timeout=60)
        assert r.status_code == 200, r.text
        tickers = [h.get("ticker", "").upper() for h in r.json()["holdings"]]
        assert "TSLA" in tickers, f"Expected TSLA in Musk's holdings; got {tickers}"

    def test_huang_ceo_contains_nvda(self, client):
        r = client.post(f"{BASE_URL}/api/people/holdings",
                        json={"name": "Jensen Huang", "role": "CEO"}, timeout=60)
        assert r.status_code == 200, r.text
        tickers = [h.get("ticker", "").upper() for h in r.json()["holdings"]]
        assert "NVDA" in tickers, f"Expected NVDA in Huang's holdings; got {tickers}"

    def test_empty_name_returns_400(self, client):
        r = client.post(f"{BASE_URL}/api/people/holdings",
                        json={"name": "", "role": "CEO"}, timeout=30)
        assert r.status_code == 400, r.text


# ============ watchlist CRUD ============
class TestWatchlist:
    def test_initial_empty(self, client):
        r = client.get(f"{BASE_URL}/api/watchlist", timeout=15)
        assert r.status_code == 200
        assert r.json() == {"tickers": []}

    def test_add_ticker(self, client):
        r = client.post(f"{BASE_URL}/api/watchlist", json={"ticker": "NVDA"}, timeout=15)
        assert r.status_code == 200
        assert "NVDA" in r.json()["tickers"]

    def test_add_idempotent(self, client):
        client.post(f"{BASE_URL}/api/watchlist", json={"ticker": "NVDA"}, timeout=15)
        r = client.post(f"{BASE_URL}/api/watchlist", json={"ticker": "NVDA"}, timeout=15)
        assert r.status_code == 200
        tickers = r.json()["tickers"]
        assert tickers.count("NVDA") == 1, f"Should be idempotent, got {tickers}"

    def test_add_lowercase_upcased(self, client):
        r = client.post(f"{BASE_URL}/api/watchlist", json={"ticker": "aapl"}, timeout=15)
        assert r.status_code == 200
        assert "AAPL" in r.json()["tickers"]

    def test_add_empty_returns_400(self, client):
        r = client.post(f"{BASE_URL}/api/watchlist", json={"ticker": ""}, timeout=15)
        assert r.status_code == 400

    def test_delete_ticker(self, client):
        client.post(f"{BASE_URL}/api/watchlist", json={"ticker": "DJT"}, timeout=15)
        r = client.delete(f"{BASE_URL}/api/watchlist/DJT", timeout=15)
        assert r.status_code == 200
        assert "DJT" not in r.json()["tickers"]
        # verify via GET
        g = client.get(f"{BASE_URL}/api/watchlist", timeout=15)
        assert "DJT" not in g.json()["tickers"]

    def test_unauth_blocked(self):
        s = requests.Session()
        r = s.get(f"{BASE_URL}/api/watchlist", timeout=15)
        assert r.status_code in (401, 403), f"Expected auth gate, got {r.status_code}"


# ============ quotes regression incl. extra symbols ============
class TestQuotesRegression:
    def test_default_quotes(self, client):
        r = client.get(f"{BASE_URL}/api/quotes", timeout=30)
        assert r.status_code == 200
        syms = [q["symbol"] for q in r.json()["quotes"]]
        for s in ["QQQ", "SPY", "NVDA", "AAPL", "MSFT", "TSLA", "AMZN", "GOOGL"]:
            assert s in syms

    def test_quotes_with_extra(self, client):
        r = client.get(f"{BASE_URL}/api/quotes",
                       params={"symbols": "QQQ,SPY,NVDA,AAPL,MSFT,TSLA,AMZN,GOOGL,DJT"}, timeout=30)
        assert r.status_code == 200
        syms = [q["symbol"] for q in r.json()["quotes"]]
        assert "DJT" in syms
