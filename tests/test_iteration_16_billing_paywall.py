"""Iteration 16 — Stripe paywall + daily quota + preview flag tests.

Seeded test session token (USER_ID / SESSION_TOKEN) must already exist in
`test_database`. Override via env ITER16_SESSION_TOKEN / ITER16_USER_ID.
"""
import os
import time
import pytest
import requests
from datetime import datetime, timedelta, timezone
from pymongo import MongoClient

BASE_URL = os.environ["REACT_APP_BACKEND_URL"].rstrip("/")
SESSION_TOKEN = os.environ.get("ITER16_SESSION_TOKEN", "test_session_iter16_1782428164876")
USER_ID = os.environ.get("ITER16_USER_ID", "test-user-iter16-1782428164876")
MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "test_database")


@pytest.fixture(scope="module")
def db():
    client = MongoClient(MONGO_URL)
    yield client[DB_NAME]
    client.close()


@pytest.fixture
def s():
    sess = requests.Session()
    sess.headers.update({
        "Content-Type": "application/json",
        "Authorization": f"Bearer {SESSION_TOKEN}",
    })
    return sess


def _clear_quota(db):
    db.daily_quota.delete_many({"user_id": USER_ID})


def _clear_pro(db):
    db.subscriptions.delete_many({"user_id": USER_ID})


def _insert_pro(db, days=10):
    end = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()
    db.subscriptions.update_one(
        {"user_id": USER_ID},
        {"$set": {
            "user_id": USER_ID,
            "plan": "pro",
            "status": "active",
            "current_period_end": end,
            "cancel_at_period_end": False,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }, "$setOnInsert": {"created_at": datetime.now(timezone.utc).isoformat()}},
        upsert=True,
    )


# ─────────────────── Billing endpoints ───────────────────
class TestBillingEndpoints:

    def test_plan_public(self):
        r = requests.get(f"{BASE_URL}/api/billing/plan", timeout=20)
        assert r.status_code == 200
        d = r.json()
        assert d["price"] == 7.99
        assert d["currency"] == "usd"
        assert d["days"] == 30
        assert isinstance(d.get("label"), str) and len(d["label"]) > 0

    def test_status_free(self, s, db):
        _clear_pro(db)
        r = s.get(f"{BASE_URL}/api/billing/status", timeout=20)
        assert r.status_code == 200
        d = r.json()
        assert d["plan"] == "free"
        assert d["status"] == "inactive"
        assert d["price"] == 7.99
        assert d["currency"] == "usd"
        assert d.get("current_period_end") in (None, "")

    def test_checkout_creates_session_and_txn(self, s, db):
        r = s.post(f"{BASE_URL}/api/billing/checkout",
                   json={"origin_url": "https://quant-trade-analysis.preview.emergentagent.com"},
                   timeout=30)
        assert r.status_code == 200, r.text
        d = r.json()
        assert "checkout.stripe.com" in d["url"]
        assert d["session_id"]
        txn = db.payment_transactions.find_one({"session_id": d["session_id"], "user_id": USER_ID})
        assert txn is not None
        assert txn["payment_status"] == "pending"
        # cache for next test
        pytest.cs_session_id = d["session_id"]

    def test_checkout_status_unpaid(self, s, db):
        sid = getattr(pytest, "cs_session_id", None)
        if not sid:
            pytest.skip("no checkout session id")
        r = s.get(f"{BASE_URL}/api/billing/checkout/{sid}", timeout=20)
        assert r.status_code == 200, r.text
        d = r.json()
        assert "payment_status" in d
        # un-paid status (open/expired/unpaid)
        assert d["payment_status"] != "paid"
        txn = db.payment_transactions.find_one({"session_id": sid, "user_id": USER_ID})
        assert txn is not None
        assert "updated_at" in txn

    def test_cancel_toggle(self, s, db):
        _insert_pro(db, days=10)
        r = s.post(f"{BASE_URL}/api/billing/cancel", json={"cancel": True}, timeout=20)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["cancel_at_period_end"] is True
        assert d["plan"] == "pro"
        r2 = s.post(f"{BASE_URL}/api/billing/cancel", json={"cancel": False}, timeout=20)
        assert r2.status_code == 200
        assert r2.json()["cancel_at_period_end"] is False
        _clear_pro(db)


# ─────────────────── Daily quota — V1-V6 analysis ───────────────────
class TestStockAnalysisQuota:

    def test_first_then_second_402(self, s, db):
        _clear_pro(db)
        _clear_quota(db)
        # 1st call — any status except 402 is OK (quota consumed pre-analysis)
        r1 = s.post(f"{BASE_URL}/api/analyze", json={"ticker": "AAPL"}, timeout=180)
        assert r1.status_code != 402, f"1st should not be 402: {r1.status_code} {r1.text[:200]}"

        # 2nd call with different ticker should be 402
        r2 = s.post(f"{BASE_URL}/api/analyze", json={"ticker": "MSFT"}, timeout=60)
        assert r2.status_code == 402, f"2nd call expected 402, got {r2.status_code}: {r2.text[:200]}"
        d = r2.json()
        detail = d["detail"]
        assert detail["code"] == "quota_exceeded"
        assert detail["feature"] == "stock_analysis"


# ─────────────────── Daily quota — Chart analysis ───────────────────
class TestChartAnalysisQuota:

    def test_first_then_second_402(self, s, db):
        _clear_pro(db)
        _clear_quota(db)
        r1 = s.post(f"{BASE_URL}/api/candles/analyze",
                    json={"symbol": "AAPL", "interval": "1d"}, timeout=120)
        assert r1.status_code != 402, f"1st should not be 402: {r1.status_code} {r1.text[:200]}"

        r2 = s.post(f"{BASE_URL}/api/candles/analyze",
                    json={"symbol": "MSFT", "interval": "1d"}, timeout=30)
        assert r2.status_code == 402, f"expected 402, got {r2.status_code}: {r2.text[:200]}"
        detail = r2.json()["detail"]
        assert detail["code"] == "quota_exceeded"
        assert detail["feature"] == "chart_analysis"


# ─────────────────── Pro bypass ───────────────────
class TestProBypass:

    def test_pro_unlimited_stock_analysis(self, s, db):
        _clear_quota(db)
        _insert_pro(db, days=10)
        # 3 in a row — none should be 402
        for t in ("AAPL", "MSFT", "GOOG"):
            r = s.post(f"{BASE_URL}/api/analyze", json={"ticker": t}, timeout=180)
            assert r.status_code != 402, f"PRO got 402 for {t}: {r.text[:200]}"

    def test_pro_unlimited_chart_analysis(self, s, db):
        _clear_quota(db)
        _insert_pro(db, days=10)
        for t in ("AAPL", "MSFT", "TSLA"):
            r = s.post(f"{BASE_URL}/api/candles/analyze",
                       json={"symbol": t, "interval": "1d"}, timeout=120)
            assert r.status_code != 402, f"PRO got 402 for {t}: {r.text[:200]}"
        _clear_pro(db)


# ─────────────────── Preview flag (paywall blur) ───────────────────
class TestPreviewFlag:

    def test_free_presidential_preview_true(self, s, db):
        _clear_pro(db)
        r = s.get(f"{BASE_URL}/api/calendar/presidential", timeout=30)
        assert r.status_code == 200, r.text
        assert r.json().get("preview") is True

    def test_free_presidential_meetings_preview_true(self, s, db):
        _clear_pro(db)
        r = s.get(f"{BASE_URL}/api/calendar/presidential-meetings", timeout=30)
        assert r.status_code == 200, r.text
        assert r.json().get("preview") is True

    def test_free_analysts_preview_true(self, s, db):
        _clear_pro(db)
        r = s.get(f"{BASE_URL}/api/analysts/AAPL", timeout=30)
        assert r.status_code == 200, r.text
        assert r.json().get("preview") is True

    def test_pro_presidential_preview_false(self, s, db):
        _insert_pro(db, days=10)
        r = s.get(f"{BASE_URL}/api/calendar/presidential", timeout=30)
        assert r.status_code == 200
        assert r.json().get("preview") is False

    def test_pro_meetings_preview_false(self, s, db):
        _insert_pro(db, days=10)
        r = s.get(f"{BASE_URL}/api/calendar/presidential-meetings", timeout=30)
        assert r.status_code == 200
        assert r.json().get("preview") is False

    def test_pro_analysts_preview_false(self, s, db):
        _insert_pro(db, days=10)
        r = s.get(f"{BASE_URL}/api/analysts/AAPL", timeout=30)
        assert r.status_code == 200
        assert r.json().get("preview") is False
        _clear_pro(db)
