"""Iteration 10 — Ticker name resolution + watchlist category rename/reorder.

Verifies:
- POST /api/analyze accepts a company name and resolves to a canonical ticker via Finnhub
- POST /api/analyze still accepts a raw ticker (no regression)
- POST /api/analyze with empty ticker returns 400
- PATCH /api/watchlist/category renames all items with `old` to `new`
- PATCH /api/watchlist/category with empty `old` returns 400
- POST /api/watchlist/reorder sorts items by requested category order
"""
import os
import pytest
import requests

BASE_URL = os.environ["REACT_APP_BACKEND_URL"].rstrip("/")
SESSION_TOKEN = os.environ.get("TEST_SESSION_TOKEN", "test_session_people_1781748718633")

TEST_TICKERS = ["AAPL", "NVDA", "BA", "LMT", "MSFT", "TSLA"]


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
    for t in TEST_TICKERS:
        client.delete(f"{BASE_URL}/api/watchlist/{t}")
    yield
    for t in TEST_TICKERS:
        client.delete(f"{BASE_URL}/api/watchlist/{t}")


# ============ /api/analyze with name resolution ============
class TestAnalyzeNameResolution:
    def test_analyze_with_company_name_apple(self, client):
        r = client.post(f"{BASE_URL}/api/analyze", json={"ticker": "Apple"}, timeout=120)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data.get("ticker") == "AAPL", f"expected AAPL got {data.get('ticker')}"
        company = (data.get("company") or "").lower()
        assert "apple" in company, f"expected Apple in company name, got {data.get('company')}"

    def test_analyze_with_company_name_nvidia(self, client):
        r = client.post(f"{BASE_URL}/api/analyze", json={"ticker": "nvidia"}, timeout=120)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data.get("ticker") == "NVDA", f"expected NVDA got {data.get('ticker')}"

    def test_analyze_with_raw_ticker_still_works(self, client):
        """Regression: raw uppercase ticker must bypass search and work."""
        r = client.post(f"{BASE_URL}/api/analyze", json={"ticker": "NVDA"}, timeout=120)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data.get("ticker") == "NVDA"

    def test_analyze_empty_ticker_returns_400(self, client):
        r = client.post(f"{BASE_URL}/api/analyze", json={"ticker": ""}, timeout=15)
        assert r.status_code == 400, r.text

    def test_analyze_whitespace_only_returns_400(self, client):
        r = client.post(f"{BASE_URL}/api/analyze", json={"ticker": "   "}, timeout=15)
        assert r.status_code == 400, r.text


# ============ PATCH /api/watchlist/category ============
class TestWatchlistRename:
    def test_rename_category_updates_all_matching_items(self, client):
        # Seed: 2 items in Technology & AI, 1 in Defense & Aerospace
        client.post(f"{BASE_URL}/api/watchlist",
                    json={"ticker": "AAPL", "category": "Technology & AI"}, timeout=15)
        client.post(f"{BASE_URL}/api/watchlist",
                    json={"ticker": "MSFT", "category": "Technology & AI"}, timeout=15)
        client.post(f"{BASE_URL}/api/watchlist",
                    json={"ticker": "BA", "category": "Defense & Aerospace"}, timeout=15)

        # Rename Technology & AI → Tech AI
        r = client.patch(f"{BASE_URL}/api/watchlist/category",
                         json={"old": "Technology & AI", "new": "Tech AI"}, timeout=15)
        assert r.status_code == 200, r.text
        body = r.json()
        cat_for = {i["ticker"]: i["category"] for i in body["items"]}
        assert cat_for.get("AAPL") == "Tech AI", f"AAPL not renamed: {cat_for}"
        assert cat_for.get("MSFT") == "Tech AI", f"MSFT not renamed: {cat_for}"
        assert cat_for.get("BA") == "Defense & Aerospace", "BA should not have changed"

        # GET to verify persistence
        g = client.get(f"{BASE_URL}/api/watchlist", timeout=15).json()
        cat_for_g = {i["ticker"]: i["category"] for i in g["items"]}
        assert cat_for_g.get("AAPL") == "Tech AI"
        assert cat_for_g.get("MSFT") == "Tech AI"

    def test_rename_with_empty_old_returns_400(self, client):
        r = client.patch(f"{BASE_URL}/api/watchlist/category",
                         json={"old": "", "new": "Whatever"}, timeout=15)
        assert r.status_code == 400, r.text

    def test_rename_with_whitespace_old_returns_400(self, client):
        r = client.patch(f"{BASE_URL}/api/watchlist/category",
                         json={"old": "   ", "new": "Whatever"}, timeout=15)
        assert r.status_code == 400, r.text


# ============ POST /api/watchlist/reorder ============
class TestWatchlistReorder:
    def test_reorder_sorts_items_by_requested_category_order(self, client):
        # Items from previous test: AAPL/MSFT in 'Tech AI', BA in 'Defense & Aerospace'
        # Add one more Defense ticker for clearer ordering check
        client.post(f"{BASE_URL}/api/watchlist",
                    json={"ticker": "LMT", "category": "Defense & Aerospace"}, timeout=15)

        r = client.post(f"{BASE_URL}/api/watchlist/reorder",
                        json={"categories": ["Defense & Aerospace", "Tech AI"]}, timeout=15)
        assert r.status_code == 200, r.text
        body = r.json()
        items = body["items"]
        # All Defense items must come before all Tech AI items
        cats_in_order = [i["category"] for i in items]
        # Find first non-Defense index
        first_non_defense = next((idx for idx, c in enumerate(cats_in_order)
                                  if c != "Defense & Aerospace"), len(cats_in_order))
        # All before first_non_defense must be Defense
        assert all(c == "Defense & Aerospace" for c in cats_in_order[:first_non_defense])
        # All from first_non_defense onward must be Tech AI (no Defense after)
        assert "Defense & Aerospace" not in cats_in_order[first_non_defense:], \
            f"Defense items appear after Tech AI: {cats_in_order}"

        # GET to confirm persistence
        g = client.get(f"{BASE_URL}/api/watchlist", timeout=15).json()
        cats_g = [i["category"] for i in g["items"]]
        first_non_defense_g = next((idx for idx, c in enumerate(cats_g)
                                    if c != "Defense & Aerospace"), len(cats_g))
        assert "Defense & Aerospace" not in cats_g[first_non_defense_g:]

    def test_reorder_unknown_categories_stay_at_end(self, client):
        # Add an item with Other category
        client.post(f"{BASE_URL}/api/watchlist",
                    json={"ticker": "TSLA", "category": "Other"}, timeout=15)
        # Reorder only by Tech AI and Defense → Other items should land at end
        r = client.post(f"{BASE_URL}/api/watchlist/reorder",
                        json={"categories": ["Tech AI", "Defense & Aerospace"]}, timeout=15)
        assert r.status_code == 200, r.text
        body = r.json()
        cats = [i["category"] for i in body["items"]]
        # The last item must be Other (unknown stays at end)
        assert cats[-1] == "Other", f"Other not at end: {cats}"

    def test_reorder_with_empty_list_returns_200(self, client):
        """Empty categories list is a no-op-ish: order is undefined but call succeeds."""
        r = client.post(f"{BASE_URL}/api/watchlist/reorder",
                        json={"categories": []}, timeout=15)
        assert r.status_code == 200, r.text
        assert "items" in r.json()
