"""FastAPI app: auth + quant analysis."""
import logging
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import APIRouter, FastAPI, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel
from starlette.middleware.cors import CORSMiddleware

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

from analysis import run_full_analysis  # noqa: E402
from auth import (  # noqa: E402
    SESSION_COOKIE,
    SESSION_DAYS,
    build_google_authorize_url,
    clear_oauth_state_cookie,
    cookie_secure,
    cookie_samesite,
    exchange_google_code,
    frontend_url,
    get_current_user,
    session_cookie_kwargs,
    set_oauth_state_cookie,
    upsert_user_and_session,
    verify_oauth_state,
)
from billing import (  # noqa: E402
    MONTHLY_ACCESS_PASS,
    MONTHLY_PRO_GENERATIONS,
    consume_daily_free_quota,
    consume_monthly_pro_quota,
    create_checkout_session,
    get_billing_status,
    get_checkout_status,
    get_monthly_generations_used,
    get_user_tier,
    handle_webhook,
    is_pro,
    set_cancel_at_period_end,
)
from candles import analyze_candles, fetch_candles  # noqa: E402
from market import (  # noqa: E402
    finnhub_company_news,
    finnhub_market_news,
    finnhub_quote,
    finnhub_recommendation_trends,
    finnhub_symbol_search,
    get_week_earnings,
    resolve_ticker,
    yfinance_analyst_targets,
)
from people import lookup_person_holdings  # noqa: E402
from referrals import (  # noqa: E402
    credit_referral_if_pending,
    get_referral_stats,
    track_referral,
)

mongo_url = os.environ["MONGO_URL"]
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ["DB_NAME"]]

app = FastAPI(title="Quant Trading Analysis API")
api = APIRouter(prefix="/api")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("quant")


# ============================ AUTH ROUTES ============================
@api.get("/auth/google")
async def auth_google_start():
    """Begin Google OAuth — redirect the browser to Google."""
    state = secrets.token_urlsafe(24)
    redirect = RedirectResponse(url=build_google_authorize_url(state), status_code=302)
    set_oauth_state_cookie(redirect, state)
    return redirect


@api.get("/auth/google/callback")
async def auth_google_callback(
    request: Request,
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
):
    """Google redirects here; set session cookie and bounce to the SPA."""
    dest = frontend_url()
    if error:
        return RedirectResponse(url=f"{dest}/?auth_error={error}", status_code=302)
    if not code or not state:
        return RedirectResponse(url=f"{dest}/?auth_error=missing_code", status_code=302)

    try:
        verify_oauth_state(request, state)
        oauth_data = await exchange_google_code(code)
        user = await upsert_user_and_session(db, oauth_data)
    except HTTPException as exc:
        detail = str(exc.detail).replace(" ", "_")[:64]
        return RedirectResponse(url=f"{dest}/?auth_error={detail}", status_code=302)
    except Exception:
        log.exception("google oauth callback failed")
        return RedirectResponse(url=f"{dest}/?auth_error=oauth_failed", status_code=302)

    redirect = RedirectResponse(url=f"{dest}/dashboard", status_code=302)
    clear_oauth_state_cookie(redirect)
    redirect.set_cookie(
        value=user["session_token"],
        max_age=SESSION_DAYS * 24 * 60 * 60,
        **session_cookie_kwargs(),
    )
    return redirect


@api.get("/auth/me")
async def auth_me(request: Request):
    user = await get_current_user(request, db)
    return {
        "user_id": user["user_id"],
        "email": user["email"],
        "name": user["name"],
        "picture": user.get("picture", ""),
    }


@api.post("/auth/logout")
async def auth_logout(request: Request, response: Response):
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        await db.user_sessions.delete_one({"session_token": token})
    response.delete_cookie(
        key=SESSION_COOKIE,
        path="/",
        secure=cookie_secure(),
        samesite=cookie_samesite(),
    )
    return {"ok": True}


# ============================ BILLING ROUTES ============================
class CheckoutCreateRequest(BaseModel):
    origin_url: str
    tier: str = "plus"


@api.post("/billing/checkout")
async def billing_checkout(req: CheckoutCreateRequest, request: Request):
    user = await get_current_user(request, db)
    api_key = (os.environ.get("STRIPE_API_KEY") or "").strip()
    if not api_key:
        raise HTTPException(status_code=500, detail="STRIPE_API_KEY not configured")
    tier = req.tier if req.tier in ("plus", "ultra") else "plus"
    from billing import price_id_for_tier
    price_id = price_id_for_tier(tier)
    if not price_id:
        raise HTTPException(status_code=500, detail=f"Price ID for tier '{tier}' not configured")
    try:
        import stripe
        stripe.api_key = api_key
        origin = req.origin_url.rstrip("/")
        existing = await db.subscriptions.find_one({"user_id": user["user_id"]})
        customer_id = existing.get("stripe_customer_id") if existing else None
        session_kwargs = dict(
            mode="subscription",
            line_items=[{"price": price_id, "quantity": 1}],
            success_url=f"{origin}/dashboard?payment=success",
            cancel_url=f"{origin}/pricing?payment=cancelled",
            client_reference_id=user["user_id"],
            metadata={"user_id": user["user_id"], "email": user["email"], "tier": tier},
            allow_promotion_codes=True,
        )
        if customer_id:
            session_kwargs["customer"] = customer_id
        else:
            session_kwargs["customer_email"] = user["email"]
        sess = stripe.checkout.Session.create(**session_kwargs)
        await db.payment_transactions.insert_one({
            "session_id": sess.id,
            "user_id": user["user_id"],
            "email": user["email"],
            "tier": tier,
            "price_id": price_id,
            "payment_status": "pending",
            "status": "initiated",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })
        return {"url": sess.url, "session_id": sess.id, "tier": tier}
    except HTTPException:
        raise
    except Exception as e:
        log.exception("checkout create failed")
        raise HTTPException(status_code=500, detail=f"Checkout failed: {e}")


@api.post("/billing/sync")
async def billing_sync(request: Request):
    """Called from the dashboard right after Stripe redirects with ?payment=success."""
    user = await get_current_user(request, db)
    try:
        return await _do_stripe_sync(user)
    except HTTPException:
        raise
    except Exception as e:
        log.exception("billing sync failed")
        raise HTTPException(status_code=500, detail=f"Sync failed: {e}")


async def _try_lazy_sync(user) -> None:
    """Best-effort background sync — silently swallow errors so the caller can
    keep serving the 402 paywall path if the sync couldn't find a subscription."""
    try:
        await _do_stripe_sync(user)
    except Exception as e:
        log.warning("lazy stripe sync failed for %s: %s", user.get("user_id"), e)


async def _do_stripe_sync(user) -> dict:
    """Pull the user's latest active subscription from Stripe (via sk_live) and
    updates local state. Works without a webhook.

    Strategy (most → least specific):
      1. Look up the user's most recent payment_transactions row (we logged it
         at checkout creation with the real session_id) → retrieve that session
         from Stripe → grab its subscription + customer ids.
      2. Fallback: lookup by customer_id we already stored.
      3. Fallback: lookup by email.
      4. Fallback: list recent Checkout Sessions, filter by client_reference_id
         (catches Payment-Link payments where we don't have a local txn row)."""
    api_key = (os.environ.get("STRIPE_API_KEY") or "").strip()
    if not api_key:
        raise HTTPException(status_code=500, detail="STRIPE_API_KEY not configured")
    import stripe
    stripe.api_key = api_key

    sub_id = None
    customer_id = None
    synced_via = "none"

    # Strategy 1: latest payment transaction for this user
    txn = await db.payment_transactions.find_one(
        {"user_id": user["user_id"]},
        sort=[("created_at", -1)],
    )
    if txn and txn.get("session_id"):
        try:
            sess = stripe.checkout.Session.retrieve(txn["session_id"])
            if (sess.get("payment_status") or "").lower() == "paid":
                sub_id = sess.get("subscription")
                customer_id = sess.get("customer")
                synced_via = "txn_row"
        except Exception as e:
            log.warning("billing_sync: could not retrieve session %s: %s", txn.get("session_id"), e)

    existing = await db.subscriptions.find_one({"user_id": user["user_id"]})

    # Strategy 2: customer_id we previously stored
    if not customer_id and existing:
        customer_id = existing.get("stripe_customer_id")
        if customer_id:
            synced_via = "stored_customer_id"

    # Strategy 3: lookup customer by email (case-insensitive Stripe match)
    if not customer_id:
        try:
            search = stripe.Customer.list(email=user["email"], limit=5)
            if search.data:
                customer_id = search.data[0].id
                synced_via = "email_lookup"
        except Exception as e:
            log.warning("billing_sync: email customer lookup failed: %s", e)

    # Strategy 4: scan recent Checkout Sessions for our client_reference_id
    # (covers Payment Link payments where we have no local txn row)
    if not customer_id:
        try:
            sessions = stripe.checkout.Session.list(limit=50)
            for s in sessions.data:
                if s.get("client_reference_id") == user["user_id"] and (s.get("payment_status") or "").lower() == "paid":
                    sub_id = s.get("subscription") or sub_id
                    customer_id = s.get("customer")
                    synced_via = "client_reference_scan"
                    break
        except Exception as e:
            log.warning("billing_sync: session scan failed: %s", e)

    if not customer_id and not sub_id:
        status = await get_billing_status(db, user)
        status["synced_via"] = synced_via
        return status

    # Resolve the active subscription
    active_sub = None
    if sub_id:
        try:
            active_sub = stripe.Subscription.retrieve(sub_id)
        except Exception as e:
            log.warning("billing_sync: could not retrieve subscription %s: %s", sub_id, e)

    if not active_sub and customer_id:
        subs = stripe.Subscription.list(customer=customer_id, status="all", limit=5)
        best = None
        for s in subs.data:
            if (s.status or "").lower() in ("active", "trialing", "past_due"):
                if best is None or (s.created or 0) > (best.created or 0):
                    best = s
        if not best and subs.data:
            best = sorted(subs.data, key=lambda x: x.created or 0)[-1]
        active_sub = best

    if active_sub:
        from billing import _extract_current_period_end
        cpe_unix = _extract_current_period_end(active_sub)
        cpe_iso = datetime.fromtimestamp(int(cpe_unix), tz=timezone.utc).isoformat() if cpe_unix else None
        status_ = (active_sub.status or "").lower()
        plan = "pro" if status_ in ("active", "trialing", "past_due") else "free"
        # Detect tier from the subscription's price id
        from billing import tier_from_price_id
        sub_price_id = None
        try:
            items = active_sub.get("items") or {}
            data = items.get("data") if isinstance(items, dict) else items.data if hasattr(items, "data") else []
            if data:
                first = data[0]
                p = first.get("price") if isinstance(first, dict) else first.price
                sub_price_id = p.get("id") if isinstance(p, dict) else getattr(p, "id", None)
        except Exception:
            pass
        tier = tier_from_price_id(sub_price_id) if plan == "pro" else "free"
        await db.subscriptions.update_one(
            {"user_id": user["user_id"]},
            {"$set": {
                "user_id": user["user_id"],
                "email": user["email"],
                "plan": plan,
                "tier": tier,
                "status": "active" if plan == "pro" else "inactive",
                "current_period_end": cpe_iso,
                "cancel_at_period_end": bool(active_sub.get("cancel_at_period_end", False)),
                "stripe_customer_id": customer_id or active_sub.get("customer"),
                "stripe_subscription_id": active_sub.id,
                "stripe_price_id": sub_price_id,
                "stripe_status": status_,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
             "$setOnInsert": {"created_at": datetime.now(timezone.utc).isoformat()}},
            upsert=True,
        )
        if txn:
            await db.payment_transactions.update_one(
                {"_id": txn["_id"]},
                {"$set": {"payment_status": "paid", "status": "complete", "processed": True, "updated_at": datetime.now(timezone.utc).isoformat()}},
            )

        # Credit referral bonus if this user was referred (idempotent — only
        # fires once per referred_user_id even if sync runs on every dashboard
        # open). Both parties get +30 days of local bonus_end.
        if plan == "pro":
            try:
                await credit_referral_if_pending(db, user["user_id"])
            except Exception as e:
                log.warning("credit_referral failed: %s", e)

    status = await get_billing_status(db, user)
    status["synced_via"] = synced_via
    return status


@api.get("/billing/checkout/{session_id}")
async def billing_checkout_status(session_id: str, request: Request):
    user = await get_current_user(request, db)
    try:
        return await get_checkout_status(db, session_id, user["user_id"])
    except HTTPException:
        raise
    except Exception as e:
        log.exception("checkout status failed")
        raise HTTPException(status_code=500, detail=f"Status check failed: {e}")


@api.get("/billing/status")
async def billing_status(request: Request):
    user = await get_current_user(request, db)
    return await get_billing_status(db, user)


class CancelRequest(BaseModel):
    cancel: bool = True


@api.post("/billing/cancel")
async def billing_cancel(req: CancelRequest, request: Request):
    user = await get_current_user(request, db)
    return await set_cancel_at_period_end(db, user, req.cancel)


@api.post("/webhook/stripe")
async def stripe_webhook(request: Request):
    body = await request.body()
    sig = request.headers.get("Stripe-Signature", "")
    host_url = str(request.base_url)
    try:
        return await handle_webhook(db, host_url, body, sig)
    except HTTPException:
        raise
    except Exception as e:
        log.exception("stripe webhook failed")
        # Always 200 to ack receipt; log the error
        return {"ok": False, "error": str(e)}


@api.get("/billing/plan")
async def billing_plan():
    """Public plan info (no auth) for the marketing/Paywall card."""
    return {
        "price": MONTHLY_ACCESS_PASS["amount"],
        "currency": MONTHLY_ACCESS_PASS["currency"],
        "label": MONTHLY_ACCESS_PASS["label"],
        "days": MONTHLY_ACCESS_PASS["days"],
    }


# ============================ REFERRAL ROUTES ============================
class ReferralTrackRequest(BaseModel):
    code: str


@api.post("/referral/track")
async def referral_track(req: ReferralTrackRequest, request: Request):
    """Called right after a new user completes auth if the visited landing
    URL had ?ref=CODE. Idempotent per referred user."""
    user = await get_current_user(request, db)
    return await track_referral(db, user, req.code)


@api.get("/referral/me")
async def referral_me(request: Request):
    user = await get_current_user(request, db)
    return await get_referral_stats(db, user)


# ============================ ANALYSIS ROUTES ============================
class AnalyzeRequest(BaseModel):
    ticker: str
    manual_price: dict | None = None


@api.post("/analyze")
async def analyze(req: AnalyzeRequest, request: Request):
    user = await get_current_user(request, db)
    raw = req.ticker.strip()
    if not raw or len(raw) > 60:
        raise HTTPException(status_code=400, detail="Invalid ticker / name")

    # Hard paywall — only Plus / Ultra can run V1-V6.
    # Self-heal: if the user appears Free locally but has an active Stripe
    # subscription (e.g. they paid seconds ago and their local doc's
    # current_period_end never got written), lazily re-sync from Stripe first.
    if not await is_pro(db, user):
        await _try_lazy_sync(user)
        if not await is_pro(db, user):
            tier = await get_user_tier(db, user)
            raise HTTPException(
                status_code=402,
                detail={
                    "code": "subscription_required",
                    "feature": "stock_analysis",
                    "current_tier": tier,
                    "message": "Subscribe to Plus ($14.99 · 15/mo) or Ultra ($23.99 · 30/mo) to unlock V1-V6 analysis.",
                },
            )
    allowed = await consume_monthly_pro_quota(db, user["user_id"])
    if not allowed:
        tier = await get_user_tier(db, user)
        from billing import TIERS as _TIERS
        raise HTTPException(
            status_code=402,
            detail={
                "code": "monthly_limit_exceeded",
                "feature": "stock_analysis",
                "current_tier": tier,
                "limit": _TIERS.get(tier, {}).get("monthly_limit", 0),
                "message": f"You've used all {_TIERS.get(tier, {}).get('monthly_limit', 0)} of your monthly {_TIERS.get(tier, {}).get('name', tier)} generations.",
            },
        )

    # Accept ticker OR company name — resolve via Finnhub search if needed
    try:
        ticker = await resolve_ticker(raw)
    except Exception:
        ticker = raw.upper()

    try:
        result = await run_full_analysis(ticker, manual_price=req.manual_price)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        log.exception("analysis failed")
        raise HTTPException(status_code=500, detail=f"Analysis failed: {e}")

    analysis_id = f"ana_{uuid.uuid4().hex[:12]}"
    record = {
        "analysis_id": analysis_id,
        "user_id": user["user_id"],
        "ticker": ticker,
        "result": result,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.analyses.insert_one(record)
    return {"analysis_id": analysis_id, **result}


@api.get("/history")
async def history(request: Request):
    user = await get_current_user(request, db)
    cursor = db.analyses.find(
        {"user_id": user["user_id"]},
        {"_id": 0, "analysis_id": 1, "ticker": 1, "created_at": 1, "result.final_signal": 1, "result.company": 1},
    ).sort("created_at", -1).limit(50)
    rows = await cursor.to_list(length=50)
    out = []
    for r in rows:
        out.append({
            "analysis_id": r["analysis_id"],
            "ticker": r["ticker"],
            "created_at": r["created_at"],
            "final_signal": (r.get("result") or {}).get("final_signal"),
            "company": (r.get("result") or {}).get("company"),
        })
    return out


@api.get("/history/{analysis_id}")
async def history_one(analysis_id: str, request: Request):
    user = await get_current_user(request, db)
    row = await db.analyses.find_one(
        {"analysis_id": analysis_id, "user_id": user["user_id"]},
        {"_id": 0},
    )
    if not row:
        raise HTTPException(status_code=404, detail="Not found")
    return {"analysis_id": row["analysis_id"], **row["result"]}


@api.delete("/history/{analysis_id}")
async def delete_history(analysis_id: str, request: Request):
    user = await get_current_user(request, db)
    r = await db.analyses.delete_one({"analysis_id": analysis_id, "user_id": user["user_id"]})
    if r.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Not found")
    return {"ok": True}


class ProfileUpdate(BaseModel):
    name: str | None = None
    picture: str | None = None


@api.patch("/users/me")
async def update_profile(req: ProfileUpdate, request: Request):
    user = await get_current_user(request, db)
    update = {}
    if req.name is not None:
        n = req.name.strip()
        if n and len(n) <= 80:
            update["name"] = n
    if req.picture is not None:
        p = req.picture.strip()
        # Cap data-URL size to ~512 KB to keep doc small
        if p and len(p) <= 700_000:
            update["picture"] = p
    if update:
        await db.users.update_one({"user_id": user["user_id"]}, {"$set": update})
    user = await db.users.find_one({"user_id": user["user_id"]}, {"_id": 0})
    return {
        "user_id": user["user_id"],
        "email": user["email"],
        "name": user.get("name", ""),
        "picture": user.get("picture", ""),
    }


@api.get("/")
async def root():
    return {"service": "quant-trading-analysis", "ok": True}


@api.get("/health")
async def health():
    """Load-balancer / Docker health check — verifies API + Mongo."""
    mongo_ok = False
    try:
        await client.admin.command("ping")
        mongo_ok = True
    except Exception:
        log.exception("health mongo ping failed")
    status = "ok" if mongo_ok else "degraded"
    payload = {
        "service": "quant-trading-analysis",
        "status": status,
        "ok": mongo_ok,
        "mongo": mongo_ok,
    }
    if not mongo_ok:
        raise HTTPException(status_code=503, detail=payload)
    return payload


# ============================ LIVE QUOTES / CANDLES ============================
DEFAULT_WATCHLIST = ["QQQ", "SPY", "NVDA", "AAPL", "MSFT", "TSLA", "AMZN", "GOOGL"]


@api.get("/quotes")
async def get_quotes(request: Request, symbols: str = ""):
    await get_current_user(request, db)
    syms = [s.strip().upper() for s in symbols.split(",") if s.strip()] if symbols else DEFAULT_WATCHLIST
    out = []
    for s in syms[:15]:
        try:
            q = await finnhub_quote(s)
            out.append({
                "symbol": s,
                "price": q.get("c"),
                "change": q.get("d"),
                "percent": q.get("dp"),
                "high": q.get("h"),
                "low": q.get("l"),
                "open": q.get("o"),
                "prev_close": q.get("pc"),
            })
        except Exception as e:
            out.append({"symbol": s, "error": str(e)})
    return {"quotes": out}


@api.get("/candles")
async def get_candles(request: Request, symbol: str, interval: str = "1d"):
    await get_current_user(request, db)
    sym = symbol.strip().upper()
    try:
        data = fetch_candles(sym, interval)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        log.exception("candles failed")
        raise HTTPException(status_code=500, detail=f"Candle fetch failed: {e}")
    return {"symbol": sym, "interval": interval, "candles": data}


@api.get("/earnings/week")
async def earnings_week(request: Request):
    """Most-anticipated earnings for THIS week (Mon-Fri). Cached per-week so the data
    auto-refreshes every Monday."""
    await get_current_user(request, db)
    today = datetime.now(timezone.utc).date()
    this_monday = today - timedelta(days=today.weekday())
    cache_key = f"mae_week:{this_monday.isoformat()}"
    try:
        cached = await db.app_cache.find_one({"key": cache_key})
        if cached and cached.get("events"):
            return {"events": cached["events"], "week_of": this_monday.isoformat()}
        events = await get_week_earnings(limit=12)
        await db.app_cache.update_one(
            {"key": cache_key},
            {"$set": {
                "key": cache_key, "events": events,
                "cached_at": datetime.now(timezone.utc).isoformat(),
            }},
            upsert=True,
        )
    except Exception as e:
        log.exception("earnings/week failed")
        raise HTTPException(status_code=500, detail=f"Earnings calendar failed: {e}")
    return {"events": events, "week_of": this_monday.isoformat()}


@api.get("/calendar/economic")
async def economic_calendar(request: Request):
    await get_current_user(request, db)
    try:
        from economic import get_economic_calendar
        result = await get_economic_calendar(db)
    except Exception as e:
        log.exception("economic calendar failed")
        raise HTTPException(status_code=500, detail=f"Economic calendar failed: {e}")
    return result


@api.get("/calendar/pulse")
async def calendar_pulse(request: Request):
    await get_current_user(request, db)
    try:
        from economic import get_pulse
        result = await get_pulse(db)
    except Exception as e:
        log.exception("pulse calendar failed")
        raise HTTPException(status_code=500, detail=f"Pulse failed: {e}")
    return result


@api.get("/calendar/presidential")
async def calendar_presidential(request: Request):
    user = await get_current_user(request, db)
    try:
        from presidential import get_presidential_events
        result = await get_presidential_events(db)
    except Exception as e:
        log.exception("presidential events failed")
        raise HTTPException(status_code=500, detail=f"Presidential events failed: {e}")
    result["preview"] = not await is_pro(db, user)
    return result


@api.get("/calendar/presidential-meetings")
async def calendar_presidential_meetings(request: Request):
    user = await get_current_user(request, db)
    try:
        from presidential_meetings import get_presidential_meetings
        result = await get_presidential_meetings(db)
    except Exception as e:
        log.exception("presidential meetings failed")
        raise HTTPException(status_code=500, detail=f"Presidential meetings failed: {e}")
    result["preview"] = not await is_pro(db, user)
    return result


@api.get("/search/symbols")
async def search_symbols(request: Request, q: str = "", limit: int = 8):
    """Autocomplete for ticker / company name. Hits Finnhub /search (cached 24h)
    with fallback to a curated local POPULAR_TICKERS list when Finnhub is
    rate-limited or returns no hits."""
    from popular_tickers import local_symbol_search

    await get_current_user(request, db)
    q = (q or "").strip()
    if len(q) < 1:
        return {"results": []}

    cache_key = f"sym_search:{q.lower()}"
    cached = await db.app_cache.find_one({"key": cache_key})
    if cached and cached.get("results"):
        try:
            cached_at = datetime.fromisoformat(cached["cached_at"].replace("Z", "+00:00"))
            age_h = (datetime.now(timezone.utc) - cached_at).total_seconds() / 3600.0
            if age_h < 24:
                return {"results": cached["results"][:limit]}
        except Exception:
            pass

    # Try Finnhub
    out = []
    seen = set()
    try:
        raw = await finnhub_symbol_search(q)
        for r in raw:
            sym = (r.get("symbol") or r.get("displaySymbol") or "").upper()
            if not sym or ":" in sym or sym in seen:
                continue
            seen.add(sym)
            out.append({
                "symbol": sym,
                "description": r.get("description") or "",
                "type": r.get("type") or "",
            })
            if len(out) >= limit:
                break
    except Exception:
        out = []

    # Always merge in local matches (keeps fast autocomplete even when Finnhub OK)
    for local in local_symbol_search(q, limit=limit):
        if local["symbol"] not in seen:
            seen.add(local["symbol"])
            out.append(local)
            if len(out) >= limit:
                break

    if out:
        await db.app_cache.update_one(
            {"key": cache_key},
            {"$set": {
                "key": cache_key,
                "results": out,
                "cached_at": datetime.now(timezone.utc).isoformat(),
            }},
            upsert=True,
        )

    return {"results": out[:limit]}


@api.get("/news")
async def get_news(request: Request, symbol: str = "", category: str = "general", days_back: int = 7):
    """If `symbol` given → per-ticker news (last `days_back` days). Else → general market."""
    await get_current_user(request, db)
    try:
        if symbol:
            sym = await resolve_ticker(symbol)
            items = await finnhub_company_news(sym, days_back=days_back, limit=30)
            return {"symbol": sym, "items": items, "source": "finnhub-company"}
        items = await finnhub_market_news(category=category, limit=30)
        return {"symbol": None, "items": items, "source": "finnhub-general"}
    except Exception as e:
        log.exception("news fetch failed")
        raise HTTPException(status_code=500, detail=f"News fetch failed: {e}")


@api.get("/screener/dividends")
async def get_dividend_screener(request: Request):
    await get_current_user(request, db)
    try:
        from screener import get_screener
        return get_screener()
    except Exception as e:
        log.exception("screener endpoint failed")
        raise HTTPException(status_code=500, detail=f"Screener failed: {e}")


@api.get("/analysts/{symbol}")
async def get_analyst_data(symbol: str, request: Request):
    """Returns price target consensus (yfinance) + recommendation trend buckets (Finnhub)."""
    user = await get_current_user(request, db)
    try:
        sym = await resolve_ticker(symbol)
        target = await yfinance_analyst_targets(sym)
        trend = await finnhub_recommendation_trends(sym)
        return {
            "symbol": sym,
            "target": target or {},
            "recommendations": trend or [],
            "preview": not await is_pro(db, user),
        }
    except Exception as e:
        log.exception("analyst data failed")
        raise HTTPException(status_code=500, detail=f"Analyst data failed: {e}")


class HighlightRange(BaseModel):
    start_time: int
    end_time: int


class CandleAnalyzeRequest(BaseModel):
    symbol: str
    interval: str = "1d"
    highlight: HighlightRange | None = None


@api.post("/candles/analyze")
async def analyze_candles_route(req: CandleAnalyzeRequest, request: Request):
    user = await get_current_user(request, db)
    sym = req.symbol.strip().upper()
    # Hard paywall — chart AI shares the tier's monthly cap with V1-V6.
    if not await is_pro(db, user):
        await _try_lazy_sync(user)
        if not await is_pro(db, user):
            tier = await get_user_tier(db, user)
            raise HTTPException(
                status_code=402,
                detail={
                    "code": "subscription_required",
                    "feature": "chart_analysis",
                    "current_tier": tier,
                    "message": "Subscribe to Plus ($14.99 · 15/mo) or Ultra ($23.99 · 30/mo) to unlock AI chart analysis.",
                },
            )
    allowed = await consume_monthly_pro_quota(db, user["user_id"])
    if not allowed:
        tier = await get_user_tier(db, user)
        from billing import TIERS as _TIERS
        raise HTTPException(
            status_code=402,
            detail={
                "code": "monthly_limit_exceeded",
                "feature": "chart_analysis",
                "current_tier": tier,
                "limit": _TIERS.get(tier, {}).get("monthly_limit", 0),
                "message": f"You've used all {_TIERS.get(tier, {}).get('monthly_limit', 0)} of your monthly {_TIERS.get(tier, {}).get('name', tier)} generations.",
            },
        )
    try:
        candles = fetch_candles(sym, req.interval)
        if not candles:
            raise HTTPException(status_code=422, detail="No candle data available")
        hl = req.highlight.model_dump() if req.highlight else None
        result = await analyze_candles(sym, req.interval, candles, highlight=hl)
        return result
    except HTTPException:
        raise
    except Exception as e:
        log.exception("candle analyze failed")
        raise HTTPException(status_code=500, detail=f"Analyze failed: {e}")


# ============================ PEOPLE / WATCHLIST ============================
class PersonHoldingsRequest(BaseModel):
    name: str
    role: str = ""


@api.post("/people/holdings")
async def people_holdings(req: PersonHoldingsRequest, request: Request):
    await get_current_user(request, db)
    if not req.name.strip():
        raise HTTPException(status_code=400, detail="Name required")
    try:
        return await lookup_person_holdings(req.name, req.role)
    except Exception as e:
        log.exception("person holdings failed")
        raise HTTPException(status_code=500, detail=f"Lookup failed: {e}")


class WatchlistAdd(BaseModel):
    ticker: str
    category: str | None = None


def _normalize_watchlist(doc: dict | None) -> list[dict]:
    if not doc:
        return []
    if doc.get("items"):
        return doc["items"]
    # Legacy: only tickers list — migrate to items shape on read
    return [{"ticker": t, "category": "Other"} for t in doc.get("tickers", [])]


@api.get("/watchlist")
async def get_watchlist(request: Request):
    user = await get_current_user(request, db)
    doc = await db.user_watchlist.find_one({"user_id": user["user_id"]}, {"_id": 0})
    items = _normalize_watchlist(doc)
    return {
        "tickers": [i["ticker"] for i in items],
        "items": items,
    }


@api.post("/watchlist")
async def add_watchlist(req: WatchlistAdd, request: Request):
    user = await get_current_user(request, db)
    ticker = req.ticker.strip().upper()
    category = (req.category or "Other").strip() or "Other"
    if not ticker or len(ticker) > 10:
        raise HTTPException(status_code=400, detail="Invalid ticker")

    doc = await db.user_watchlist.find_one({"user_id": user["user_id"]}, {"_id": 0})
    items = _normalize_watchlist(doc)
    if not any(i["ticker"] == ticker for i in items):
        items.append({"ticker": ticker, "category": category})
    else:
        # Update category if a new (non-Other) one is provided
        for i in items:
            if i["ticker"] == ticker and category != "Other":
                i["category"] = category
    await db.user_watchlist.update_one(
        {"user_id": user["user_id"]},
        {"$set": {"items": items, "tickers": [i["ticker"] for i in items]},
         "$setOnInsert": {"user_id": user["user_id"]}},
        upsert=True,
    )
    return {"tickers": [i["ticker"] for i in items], "items": items}


@api.delete("/watchlist/{ticker}")
async def remove_watchlist(ticker: str, request: Request):
    user = await get_current_user(request, db)
    t = ticker.strip().upper()
    doc = await db.user_watchlist.find_one({"user_id": user["user_id"]}, {"_id": 0})
    items = [i for i in _normalize_watchlist(doc) if i["ticker"] != t]
    await db.user_watchlist.update_one(
        {"user_id": user["user_id"]},
        {"$set": {"items": items, "tickers": [i["ticker"] for i in items]}},
    )
    return {"tickers": [i["ticker"] for i in items], "items": items}


class CategoryRename(BaseModel):
    old: str
    new: str


@api.patch("/watchlist/category")
async def rename_category(req: CategoryRename, request: Request):
    user = await get_current_user(request, db)
    old = req.old.strip()
    new = req.new.strip() or "Other"
    if not old:
        raise HTTPException(status_code=400, detail="Old category required")
    doc = await db.user_watchlist.find_one({"user_id": user["user_id"]}, {"_id": 0})
    items = _normalize_watchlist(doc)
    for i in items:
        if i.get("category") == old:
            i["category"] = new
    await db.user_watchlist.update_one(
        {"user_id": user["user_id"]},
        {"$set": {"items": items, "tickers": [i["ticker"] for i in items]}},
        upsert=True,
    )
    return {"tickers": [i["ticker"] for i in items], "items": items}


class CategoryReorder(BaseModel):
    categories: list[str]


@api.post("/watchlist/reorder")
async def reorder_categories(req: CategoryReorder, request: Request):
    user = await get_current_user(request, db)
    order = req.categories or []
    doc = await db.user_watchlist.find_one({"user_id": user["user_id"]}, {"_id": 0})
    items = _normalize_watchlist(doc)
    # Sort items so they appear in the requested category order; unknown categories at end (orig order)
    rank = {c: i for i, c in enumerate(order)}
    items_sorted = sorted(items, key=lambda x: rank.get(x.get("category", "Other"), 10_000))
    await db.user_watchlist.update_one(
        {"user_id": user["user_id"]},
        {"$set": {"items": items_sorted, "tickers": [i["ticker"] for i in items_sorted]}},
        upsert=True,
    )
    return {"tickers": [i["ticker"] for i in items_sorted], "items": items_sorted}


class ItemMove(BaseModel):
    category: str


@api.patch("/watchlist/item/{ticker}")
async def move_item(ticker: str, req: ItemMove, request: Request):
    user = await get_current_user(request, db)
    t = ticker.strip().upper()
    new_cat = (req.category or "Other").strip() or "Other"
    doc = await db.user_watchlist.find_one({"user_id": user["user_id"]}, {"_id": 0})
    items = _normalize_watchlist(doc)
    found = False
    for i in items:
        if i["ticker"] == t:
            i["category"] = new_cat
            found = True
            break
    if not found:
        raise HTTPException(status_code=404, detail="Ticker not in watchlist")
    await db.user_watchlist.update_one(
        {"user_id": user["user_id"]},
        {"$set": {"items": items, "tickers": [i["ticker"] for i in items]}},
    )
    return {"tickers": [i["ticker"] for i in items], "items": items}



app.include_router(api)

_cors_origins = [
    o.strip()
    for o in os.environ.get("CORS_ORIGINS", "http://localhost:3000").split(",")
    if o.strip() and o.strip() != "*"
]
if not _cors_origins:
    _cors_origins = ["http://localhost:3000"]

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=_cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("shutdown")
async def shutdown():
    client.close()
