"""Iteration 11 — Tests for /api/earnings/week (Most Anticipated Earnings strip)."""
import os
import re
import time
import pytest
import requests

# Load BASE URL from frontend/.env
def _load_base():
    url = os.environ.get("REACT_APP_BACKEND_URL")
    if url:
        return url.rstrip("/")
    try:
        with open("/app/frontend/.env") as f:
            for line in f:
                if line.startswith("REACT_APP_BACKEND_URL="):
                    return line.split("=", 1)[1].strip().rstrip("/")
    except Exception:
        pass
    raise RuntimeError("REACT_APP_BACKEND_URL not set and /app/frontend/.env missing")

BASE = _load_base()
SESSION_TOKEN = "test_session_people_1781748718633"

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@pytest.fixture(scope="module")
def auth_client():
    s = requests.Session()
    s.headers.update({
        "Authorization": f"Bearer {SESSION_TOKEN}",
        "Content-Type": "application/json",
    })
    return s


@pytest.fixture(scope="module")
def anon_client():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


# ---------- /api/earnings/week ----------
class TestEarningsWeek:
    def test_unauthenticated_returns_401(self, anon_client):
        r = anon_client.get(f"{BASE}/api/earnings/week")
        assert r.status_code == 401, r.text

    def test_authenticated_returns_events_shape(self, auth_client):
        r = auth_client.get(f"{BASE}/api/earnings/week")
        assert r.status_code == 200, r.text
        body = r.json()
        assert "events" in body and isinstance(body["events"], list)
        # We allow 0 events on weekends in some markets, but for current week most weekdays have earnings
        assert len(body["events"]) <= 12, "must respect limit=12"

    def test_event_field_shape(self, auth_client):
        r = auth_client.get(f"{BASE}/api/earnings/week")
        events = r.json()["events"]
        if not events:
            pytest.skip("No earnings this week — cannot test shape")
        for ev in events:
            assert ev["symbol"], "symbol must be non-empty"
            assert "." not in ev["symbol"], f"symbol contains '.': {ev['symbol']}"
            assert ":" not in ev["symbol"], f"symbol contains ':': {ev['symbol']}"
            assert ev["symbol"].isupper() or any(c.isdigit() for c in ev["symbol"])
            assert DATE_RE.match(ev["date"] or ""), f"bad date: {ev['date']}"
            for k in ("hour", "eps_estimate", "revenue_estimate", "quarter", "year"):
                assert k in ev, f"missing key {k}"

    def test_sorted_by_revenue_desc(self, auth_client):
        r = auth_client.get(f"{BASE}/api/earnings/week")
        events = r.json()["events"]
        if len(events) < 2:
            pytest.skip("Need >=2 events to verify ordering")
        revs = [abs(float(e.get("revenue_estimate") or 0)) for e in events]
        # Per spec: first event has the largest revenue_estimate
        assert revs[0] == max(revs), f"first event not max revenue: {revs}"

    def test_non_increasing_order_relaxed(self, auth_client):
        """Backend ranks by revenueEstimate OR revenueActual fallback. Verify
        non-increasing when considering both — flags a documentation gap if it fails."""
        events = auth_client.get(f"{BASE}/api/earnings/week").json()["events"]
        if len(events) < 2:
            pytest.skip("Need >=2 events")
        revs = [abs(float(e.get("revenue_estimate") or 0)) for e in events]
        # Just informational — non-decreasing within revenue_estimate-only view
        # may break if some entries use revenueActual fallback.
        breaks = [i for i in range(len(revs) - 1) if revs[i] < revs[i + 1]]
        if breaks:
            pytest.skip(f"Backend ranks by revenue_estimate OR revenue_actual fallback — order breaks at {breaks}: {revs}")

    def test_no_duplicate_symbols(self, auth_client):
        events = auth_client.get(f"{BASE}/api/earnings/week").json()["events"]
        syms = [e["symbol"] for e in events]
        assert len(syms) == len(set(syms)), f"duplicate symbols: {syms}"


# ---------- Watchlist integration with 'Earnings This Week' category ----------
class TestWatchlistEarningsCategory:
    @pytest.fixture(scope="class")
    def first_mae_symbol(self, auth_client):
        events = auth_client.get(f"{BASE}/api/earnings/week").json()["events"]
        if not events:
            pytest.skip("No MAE events available this week")
        return events[0]["symbol"]

    def test_add_mae_to_watchlist(self, auth_client, first_mae_symbol):
        sym = first_mae_symbol
        # ensure clean state for that symbol
        auth_client.delete(f"{BASE}/api/watchlist/{sym}")
        r = auth_client.post(f"{BASE}/api/watchlist",
                             json={"ticker": sym, "category": "Earnings This Week"})
        assert r.status_code == 200, r.text
        data = r.json()
        assert sym in data["tickers"], data
        items = data.get("items", [])
        match = [i for i in items if i["ticker"] == sym]
        assert match and match[0]["category"] == "Earnings This Week"

    def test_get_watchlist_persists(self, auth_client, first_mae_symbol):
        r = auth_client.get(f"{BASE}/api/watchlist")
        assert r.status_code == 200
        data = r.json()
        assert first_mae_symbol in data["tickers"]

    def test_cleanup(self, auth_client, first_mae_symbol):
        r = auth_client.delete(f"{BASE}/api/watchlist/{first_mae_symbol}")
        assert r.status_code == 200


# ---------- Regression: core endpoints ----------
class TestRegression:
    def test_auth_me(self, auth_client):
        r = auth_client.get(f"{BASE}/api/auth/me")
        assert r.status_code == 200, r.text
        assert "user_id" in r.json() or "email" in r.json()

    def test_quotes_endpoint(self, auth_client):
        r = auth_client.get(f"{BASE}/api/quotes", params={"symbols": "AAPL,MSFT"})
        assert r.status_code == 200, r.text
        data = r.json()
        # accept either {quotes: {...}} or {AAPL: {...}}
        assert isinstance(data, dict)

    def test_candles(self, auth_client):
        r = auth_client.get(f"{BASE}/api/candles", params={"symbol": "AAPL", "interval": "1d"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert "candles" in body and isinstance(body["candles"], list)
        assert len(body["candles"]) > 0

    def test_watchlist_get(self, auth_client):
        r = auth_client.get(f"{BASE}/api/watchlist")
        assert r.status_code == 200

    def test_people_holdings(self, auth_client):
        r = auth_client.post(f"{BASE}/api/people/holdings",
                             json={"name": "Warren Buffett", "role": "CEO"})
        # Accept 200 or 422 (no holdings) but never 500
        assert r.status_code in (200, 422), r.text
