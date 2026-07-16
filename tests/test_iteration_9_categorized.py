"""Iteration 9 — Hard-coded Trump portfolio + categorized watchlist schema.

Verifies:
- POST /api/people/holdings for Donald Trump / Trump returns deterministic 5-category portfolio
- Categories include expected tickers from the spec
- Backwards-compat flat `holdings` includes `category` per row
- POST /api/people/holdings (Musk/CEO) → categories array via Claude (TSLA present)
- POST /api/people/holdings (NVIDIA/Corp) → categories array via Claude
- Watchlist: items[] with category, idempotency, delete, legacy migration
"""
import os
import pytest
import requests

BASE_URL = os.environ["REACT_APP_BACKEND_URL"].rstrip("/")
SESSION_TOKEN = os.environ.get("TEST_SESSION_TOKEN", "test_session_people_1781748718633")

TRUMP_EXPECTED = {
    "Technology & AI": {"AAPL", "NVDA", "MSFT", "ORCL", "DELL", "AVGO"},
    "Defense & Aerospace": {"BA", "LMT", "NOC", "PLTR"},
    "Finance": {"OBDC"},
    "Retail & Consumer": {"COST", "WMT", "KO"},
    "Media": {"DJT"},
}


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
    for t in ["AAPL", "BA", "DJT", "NVDA", "MSFT", "ORCL", "DELL", "AVGO", "LMT",
              "NOC", "PLTR", "OBDC", "COST", "WMT", "KO", "TSLA"]:
        client.delete(f"{BASE_URL}/api/watchlist/{t}")
    yield
    for t in ["AAPL", "BA", "DJT", "NVDA", "MSFT", "ORCL", "DELL", "AVGO", "LMT",
              "NOC", "PLTR", "OBDC", "COST", "WMT", "KO", "TSLA"]:
        client.delete(f"{BASE_URL}/api/watchlist/{t}")


# ============ people/holdings — categorized ============
class TestTrumpHardcoded:
    def test_donald_trump_president_returns_all_categories(self, client):
        r = client.post(f"{BASE_URL}/api/people/holdings",
                        json={"name": "Donald Trump", "role": "President"}, timeout=15)
        assert r.status_code == 200, r.text
        data = r.json()
        assert "categories" in data, "missing categories[] in response"
        assert isinstance(data["categories"], list)
        assert len(data["categories"]) == 5, f"expected 5 categories, got {len(data['categories'])}"
        names = {c["name"] for c in data["categories"]}
        for expected in TRUMP_EXPECTED:
            assert expected in names, f"missing category {expected} (got {names})"

        # Each category contains the expected tickers
        cat_map = {c["name"]: {h["ticker"] for h in c.get("holdings", []) if h.get("ticker")}
                   for c in data["categories"]}
        for cat, tickers in TRUMP_EXPECTED.items():
            missing = tickers - cat_map[cat]
            assert not missing, f"{cat} missing tickers {missing}; got {cat_map[cat]}"

    def test_trump_short_form_also_hardcoded(self, client):
        """name='Trump' role='President' must also hit the hardcoded path."""
        r = client.post(f"{BASE_URL}/api/people/holdings",
                        json={"name": "Trump", "role": "President"}, timeout=15)
        assert r.status_code == 200, r.text
        data = r.json()
        assert len(data["categories"]) == 5
        cat_names = {c["name"] for c in data["categories"]}
        assert "Media" in cat_names

    def test_trump_flat_holdings_includes_category(self, client):
        r = client.post(f"{BASE_URL}/api/people/holdings",
                        json={"name": "Donald Trump", "role": "President"}, timeout=15)
        data = r.json()
        assert "holdings" in data and isinstance(data["holdings"], list)
        assert len(data["holdings"]) >= 15  # 6+4+1+3+1=15
        for h in data["holdings"]:
            assert "category" in h, f"flat holding missing category: {h}"
            assert "ticker" in h

    def test_trump_deterministic_no_claude(self, client):
        """Second call returns same shape instantly (deterministic / no LLM)."""
        r1 = client.post(f"{BASE_URL}/api/people/holdings",
                         json={"name": "Donald Trump", "role": "President"}, timeout=15).json()
        r2 = client.post(f"{BASE_URL}/api/people/holdings",
                         json={"name": "Donald Trump", "role": "President"}, timeout=15).json()
        assert r1["categories"] == r2["categories"]


class TestClaudePersonCategorized:
    def test_musk_ceo_returns_categories_with_tsla(self, client):
        r = client.post(f"{BASE_URL}/api/people/holdings",
                        json={"name": "Elon Musk", "role": "CEO"}, timeout=60)
        assert r.status_code == 200, r.text
        data = r.json()
        assert "categories" in data and isinstance(data["categories"], list)
        assert len(data["categories"]) >= 1, f"no categories returned: {data}"
        # at least one category contains TSLA
        found = any(
            any(h.get("ticker", "").upper() == "TSLA" for h in c.get("holdings", []))
            for c in data["categories"]
        )
        assert found, f"TSLA not found in any category: {data['categories']}"

    def test_nvidia_corp_returns_categories(self, client):
        r = client.post(f"{BASE_URL}/api/people/holdings",
                        json={"name": "NVIDIA", "role": "Corp"}, timeout=60)
        assert r.status_code == 200, r.text
        data = r.json()
        assert "categories" in data and isinstance(data["categories"], list)
        # Corp may return holdings; categories may be empty if Claude doesn't know — accept either
        # But response shape must be present
        assert "holdings" in data


# ============ watchlist with categories ============
class TestWatchlistCategorized:
    def test_get_returns_tickers_and_items(self, client):
        r = client.get(f"{BASE_URL}/api/watchlist", timeout=15)
        assert r.status_code == 200
        body = r.json()
        assert "tickers" in body and "items" in body
        assert isinstance(body["tickers"], list) and isinstance(body["items"], list)

    def test_add_with_category(self, client):
        r = client.post(f"{BASE_URL}/api/watchlist",
                        json={"ticker": "AAPL", "category": "Technology & AI"}, timeout=15)
        assert r.status_code == 200, r.text
        body = r.json()
        assert "AAPL" in body["tickers"]
        match = [i for i in body["items"] if i["ticker"] == "AAPL"]
        assert match and match[0]["category"] == "Technology & AI"

    def test_add_second_category(self, client):
        r = client.post(f"{BASE_URL}/api/watchlist",
                        json={"ticker": "BA", "category": "Defense & Aerospace"}, timeout=15)
        assert r.status_code == 200, r.text
        body = r.json()
        assert set(body["tickers"]) >= {"AAPL", "BA"}
        cat_for = {i["ticker"]: i["category"] for i in body["items"]}
        assert cat_for["AAPL"] == "Technology & AI"
        assert cat_for["BA"] == "Defense & Aerospace"

    def test_add_idempotent_preserves_count(self, client):
        r = client.post(f"{BASE_URL}/api/watchlist", json={"ticker": "AAPL"}, timeout=15)
        assert r.status_code == 200
        body = r.json()
        assert body["tickers"].count("AAPL") == 1
        # category should remain Technology & AI (no "Other" overwrite)
        cat_for = {i["ticker"]: i["category"] for i in body["items"]}
        assert cat_for.get("AAPL") == "Technology & AI", f"category overwritten: {cat_for}"

    def test_delete_one_remains(self, client):
        r = client.delete(f"{BASE_URL}/api/watchlist/AAPL", timeout=15)
        assert r.status_code == 200
        body = r.json()
        assert "AAPL" not in body["tickers"]
        assert "BA" in body["tickers"]
        # GET to verify persistence
        g = client.get(f"{BASE_URL}/api/watchlist", timeout=15).json()
        assert "AAPL" not in g["tickers"] and "BA" in g["tickers"]

    def test_legacy_migration_to_items(self, client):
        """Inject a legacy {tickers:[...]} doc with no items, then GET — items[] must be synthesized."""
        from pymongo import MongoClient
        mc = MongoClient(os.environ["MONGO_URL"])
        dbn = os.environ["DB_NAME"]
        db = mc[dbn]
        # Find this user (the one matching SESSION_TOKEN)
        sess = db.user_sessions.find_one({"session_token": SESSION_TOKEN})
        assert sess, "session not found"
        uid = sess["user_id"]
        # Write legacy shape directly
        db.user_watchlist.update_one(
            {"user_id": uid},
            {"$set": {"tickers": ["LEGACY1", "LEGACY2"]}, "$unset": {"items": ""}},
            upsert=True,
        )
        r = client.get(f"{BASE_URL}/api/watchlist", timeout=15)
        assert r.status_code == 200
        body = r.json()
        assert set(body["tickers"]) >= {"LEGACY1", "LEGACY2"}
        cat_for = {i["ticker"]: i["category"] for i in body["items"]}
        assert cat_for.get("LEGACY1") == "Other"
        assert cat_for.get("LEGACY2") == "Other"
        # cleanup legacy entries
        client.delete(f"{BASE_URL}/api/watchlist/LEGACY1")
        client.delete(f"{BASE_URL}/api/watchlist/LEGACY2")
        mc.close()
