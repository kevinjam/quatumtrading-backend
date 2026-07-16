"""Alpha Vantage + Finnhub + yfinance price, institutional, insider and earnings fetchers."""
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
import yfinance as yf

ALPHAVANTAGE_KEY = os.environ.get("ALPHAVANTAGE_KEY", "")
FINNHUB_KEY = os.environ.get("FINNHUB_KEY", "")


async def alphavantage_overview(symbol: str) -> dict:
    url = "https://www.alphavantage.co/query"
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(url, params={"function": "OVERVIEW", "symbol": symbol, "apikey": ALPHAVANTAGE_KEY})
        r.raise_for_status()
        return r.json() or {}


async def alphavantage_quote(symbol: str) -> dict:
    url = "https://www.alphavantage.co/query"
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(url, params={"function": "GLOBAL_QUOTE", "symbol": symbol, "apikey": ALPHAVANTAGE_KEY})
        r.raise_for_status()
        return r.json().get("Global Quote", {}) or {}


async def finnhub_quote(symbol: str) -> dict:
    url = "https://finnhub.io/api/v1/quote"
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(url, params={"symbol": symbol, "token": FINNHUB_KEY})
        r.raise_for_status()
        return r.json() or {}


async def finnhub_metric(symbol: str) -> dict:
    url = "https://finnhub.io/api/v1/stock/metric"
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(url, params={"symbol": symbol, "metric": "all", "token": FINNHUB_KEY})
        r.raise_for_status()
        return r.json().get("metric", {}) or {}


async def finnhub_profile(symbol: str) -> dict:
    url = "https://finnhub.io/api/v1/stock/profile2"
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(url, params={"symbol": symbol, "token": FINNHUB_KEY})
        r.raise_for_status()
        return r.json() or {}


async def get_price_data(symbol: str) -> dict:
    """Finnhub-primary. Falls back to Alpha Vantage then yfinance.
    Current price is treated as an *estimate of last close* if live quote is missing
    (markets closed / IPO / thinly traded ticker)."""
    high_52w = low_52w = current = None
    company_name = None
    source_notes = []

    try:
        metric = await finnhub_metric(symbol)
        if metric.get("52WeekHigh") is not None:
            high_52w = float(metric["52WeekHigh"])
        if metric.get("52WeekLow") is not None:
            low_52w = float(metric["52WeekLow"])
        source_notes.append("finnhub.metric")
    except Exception:
        pass

    try:
        profile = await finnhub_profile(symbol)
        company_name = profile.get("name")
    except Exception:
        pass

    try:
        q = await finnhub_quote(symbol)
        c = q.get("c")
        if c is not None and float(c) > 0:
            current = float(c)
            source_notes.append("finnhub.quote")
    except Exception:
        pass

    if high_52w is None or low_52w is None or current is None:
        try:
            ov = await alphavantage_overview(symbol)
            if high_52w is None and ov.get("52WeekHigh"):
                high_52w = float(ov["52WeekHigh"])
            if low_52w is None and ov.get("52WeekLow"):
                low_52w = float(ov["52WeekLow"])
            if not company_name and ov.get("Name"):
                company_name = ov["Name"]
        except Exception:
            pass

    if high_52w is None or low_52w is None or current is None:
        try:
            t = yf.Ticker(symbol)
            df = t.history(period="1y", interval="1d", auto_adjust=False)
            if not df.empty:
                if high_52w is None:
                    high_52w = float(df["High"].max())
                if low_52w is None:
                    low_52w = float(df["Low"].min())
                if current is None:
                    current = float(df["Close"].iloc[-1])
                    source_notes.append("yfinance.last_close (estimate)")
            if not company_name:
                info = getattr(t, "info", {}) or {}
                company_name = info.get("shortName") or info.get("longName")
        except Exception:
            pass

    if not all([high_52w, low_52w, current]):
        raise ValueError(
            f"Incomplete price data for {symbol}: high={high_52w} low={low_52w} current={current}"
        )

    return {
        "high_52w": high_52w,
        "low_52w": low_52w,
        "current": current,
        "company_name": company_name,
        "price_source": ", ".join(source_notes) or "unknown",
    }


async def finnhub_insider_transactions(symbol: str) -> dict:
    """Returns V4B: insider sell$ / buy$ * 100 over the last ~6 months."""
    six_months_ago = (datetime.now(timezone.utc) - timedelta(days=180)).strftime("%Y-%m-%d")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    url = "https://finnhub.io/api/v1/stock/insider-transactions"
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(url, params={"symbol": symbol, "from": six_months_ago, "to": today, "token": FINNHUB_KEY})
        if r.status_code != 200:
            return {"signal": "Pending", "ratio": None, "note": f"Insider data unavailable (HTTP {r.status_code})"}
        rows = r.json().get("data", []) or []

    buy_dollars = 0.0
    sell_dollars = 0.0
    for row in rows:
        try:
            share_change = float(row.get("change") or 0)
            price = float(row.get("transactionPrice") or 0)
            value = abs(share_change) * price
            if share_change > 0:
                buy_dollars += value
            elif share_change < 0:
                sell_dollars += value
        except Exception:
            continue

    if buy_dollars == 0 and sell_dollars == 0:
        return {"signal": "Pending", "ratio": None, "note": "No insider transactions in last 180 days",
                "buy_dollars": 0, "sell_dollars": 0}
    if buy_dollars == 0:
        ratio = 9999.0
    else:
        ratio = sell_dollars / buy_dollars * 100
    signal = "Negative" if ratio >= 100 else "Positive"
    return {
        "signal": signal,
        "ratio": ratio,
        "note": f"Insider buys ${buy_dollars:,.0f} vs sells ${sell_dollars:,.0f} (last 180d)",
        "buy_dollars": buy_dollars,
        "sell_dollars": sell_dollars,
    }


async def finnhub_institutional(symbol: str) -> dict:
    """V4A institutional flow. Finnhub free tier returns 403 on /stock/ownership.
    Fall back to fund-ownership which is sometimes accessible."""
    url = "https://finnhub.io/api/v1/stock/ownership"
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(url, params={"symbol": symbol, "limit": 50, "token": FINNHUB_KEY})
        if r.status_code != 200:
            return {"signal": "Pending", "ratio": None, "inflows": None, "outflows": None,
                    "note": f"Institutional ownership requires paid tier (HTTP {r.status_code})"}
        data = r.json() or {}
    rows = data.get("ownership", []) or []
    if not rows:
        return {"signal": "Pending", "ratio": None, "inflows": None, "outflows": None,
                "note": "No institutional ownership rows returned"}

    inflow_shares = 0.0
    outflow_shares = 0.0
    for row in rows:
        try:
            chg = float(row.get("change") or 0)
            if chg > 0:
                inflow_shares += chg
            elif chg < 0:
                outflow_shares += abs(chg)
        except Exception:
            continue
    if inflow_shares == 0 and outflow_shares == 0:
        return {"signal": "Pending", "ratio": None, "inflows": "0", "outflows": "0",
                "note": "No institutional position changes"}
    ratio = (outflow_shares / inflow_shares * 100) if inflow_shares > 0 else 9999.0
    signal = "Negative" if ratio >= 100 else "Positive"
    return {
        "signal": signal,
        "ratio": ratio,
        "inflows": f"{inflow_shares:,.0f} shares",
        "outflows": f"{outflow_shares:,.0f} shares",
        "note": f"Recent 13F changes — inflows {inflow_shares:,.0f}, outflows {outflow_shares:,.0f}",
    }


async def finnhub_symbol_search(q: str) -> list[dict]:
    url = "https://finnhub.io/api/v1/search"
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(url, params={"q": q, "exchange": "US", "token": FINNHUB_KEY})
        if r.status_code != 200:
            return []
        return r.json().get("result", []) or []


async def resolve_ticker(query: str) -> str:
    """Accept either a ticker symbol or a company/stock name. Returns a canonical ticker.
    Always queries Finnhub /search and trusts the best match."""
    q = (query or "").strip()
    if not q:
        raise ValueError("Empty ticker / query")

    results = await finnhub_symbol_search(q)
    upper = q.upper()

    # Exact symbol/displaySymbol match — user typed a real ticker
    for r in results:
        sym = (r.get("symbol") or "").upper()
        ds = (r.get("displaySymbol") or "").upper()
        if sym == upper or ds == upper:
            return sym or ds

    # Prefer Common Stock / ETF (allow dot in symbol e.g. BRK.A/BRK.B)
    for r in results:
        sym = (r.get("symbol") or "").upper()
        if not sym or ":" in sym:
            continue
        if (r.get("type") or "").lower() in ("common stock", "etp", "etf"):
            return sym

    # First clean result
    for r in results:
        sym = (r.get("symbol") or "").upper()
        if sym and ":" not in sym:
            return sym

    # No hits — fall back to upper-case input
    return upper


async def get_next_earnings_date(symbol: str) -> Optional[str]:
    """Try Finnhub earnings calendar."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    far = (datetime.now(timezone.utc) + timedelta(days=120)).strftime("%Y-%m-%d")
    url = "https://finnhub.io/api/v1/calendar/earnings"
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(url, params={"from": today, "to": far, "symbol": symbol, "token": FINNHUB_KEY})
        if r.status_code != 200:
            return None
        data = r.json() or {}
    events = data.get("earningsCalendar", []) or []
    if events:
        return events[0].get("date")
    return None


async def get_week_earnings(limit: int = 12) -> list[dict]:
    """Return the most-anticipated earnings for THIS WEEK (Mon-Fri).
    Ranked by revenue estimate as a proxy for company size. Each event is enriched with
    cash-to-debt ratio (yfinance .info)."""
    today = datetime.now(timezone.utc).date()
    this_monday = today - timedelta(days=today.weekday())
    this_friday = this_monday + timedelta(days=4)
    url = "https://finnhub.io/api/v1/calendar/earnings"
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(url, params={
            "from": this_monday.isoformat(),
            "to": this_friday.isoformat(),
            "token": FINNHUB_KEY,
        })
        if r.status_code != 200:
            return []
        data = r.json() or {}

    events = data.get("earningsCalendar", []) or []

    def score(e):
        rev = e.get("revenueEstimate")
        try:
            return abs(float(rev)) if rev is not None else 0
        except Exception:
            return 0

    events = sorted(events, key=score, reverse=True)
    out = []
    seen = set()
    for e in events:
        sym = (e.get("symbol") or "").upper()
        if not sym or sym in seen or "." in sym or ":" in sym:
            continue
        seen.add(sym)
        out.append({
            "symbol": sym,
            "date": e.get("date"),
            "hour": e.get("hour"),
            "eps_estimate": e.get("epsEstimate"),
            "revenue_estimate": e.get("revenueEstimate"),
            "quarter": e.get("quarter"),
            "year": e.get("year"),
        })
        if len(out) >= limit:
            break

    # Enrich with cash/debt ratios in parallel
    import asyncio
    cash_debt_list = await asyncio.gather(
        *[asyncio.to_thread(_fetch_cash_debt, ev["symbol"]) for ev in out],
        return_exceptions=True,
    )
    for ev, cd in zip(out, cash_debt_list):
        if isinstance(cd, dict):
            ev["cash_debt"] = cd
        else:
            ev["cash_debt"] = {"cash": None, "debt": None, "ratio": None}
    return out


def _fetch_cash_debt(symbol: str) -> dict:
    try:
        info = getattr(yf.Ticker(symbol), "info", {}) or {}
        cash = info.get("totalCash")
        debt = info.get("totalDebt")
        if cash is None or debt is None or float(debt) <= 0:
            return {"cash": cash, "debt": debt, "ratio": None}
        return {
            "cash": float(cash),
            "debt": float(debt),
            "ratio": round(float(cash) / float(debt), 4),
        }
    except Exception:
        return {"cash": None, "debt": None, "ratio": None}


# ───── News (Finnhub) ─────
async def finnhub_market_news(category: str = "general", limit: int = 25) -> list[dict]:
    """General market news. Finnhub free tier supports: general, forex, crypto, merger."""
    url = "https://finnhub.io/api/v1/news"
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(url, params={"category": category, "token": FINNHUB_KEY})
        if r.status_code != 200:
            return []
        data = r.json() or []
    out = []
    for n in data[:limit]:
        out.append({
            "id": n.get("id"),
            "headline": n.get("headline") or "",
            "summary": (n.get("summary") or "")[:400],
            "source": n.get("source") or "",
            "url": n.get("url") or "",
            "image": n.get("image") or "",
            "datetime": n.get("datetime"),  # unix seconds
            "related": n.get("related") or "",  # comma-separated tickers
            "category": n.get("category") or "",
        })
    return out


async def finnhub_company_news(symbol: str, days_back: int = 7, limit: int = 25) -> list[dict]:
    """Per-ticker news for the last N days."""
    today = datetime.now(timezone.utc).date()
    frm = (today - timedelta(days=days_back)).isoformat()
    to = today.isoformat()
    url = "https://finnhub.io/api/v1/company-news"
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(url, params={"symbol": symbol, "from": frm, "to": to, "token": FINNHUB_KEY})
        if r.status_code != 200:
            return []
        data = r.json() or []
    out = []
    for n in data[:limit]:
        out.append({
            "id": n.get("id"),
            "headline": n.get("headline") or "",
            "summary": (n.get("summary") or "")[:400],
            "source": n.get("source") or "",
            "url": n.get("url") or "",
            "image": n.get("image") or "",
            "datetime": n.get("datetime"),
            "related": n.get("related") or symbol,
            "category": n.get("category") or "company",
        })
    return out


# ───── Price targets + recommendations (Finnhub) ─────
async def finnhub_price_target(symbol: str) -> dict:
    """DEPRECATED — Finnhub /price-target is premium-only. Use yfinance_analyst_targets()."""
    return {}


async def yfinance_analyst_targets(symbol: str) -> dict:
    """Pull analyst price targets + consensus from yfinance .info (free).
    Returns: {high, low, mean, median, num_analysts, recommendation_key, current_price}."""
    import asyncio
    import yfinance as yf

    def _sync():
        try:
            t = yf.Ticker(symbol)
            info = t.info or {}
            return {
                "high": info.get("targetHighPrice"),
                "low": info.get("targetLowPrice"),
                "mean": info.get("targetMeanPrice"),
                "median": info.get("targetMedianPrice"),
                "num_analysts": info.get("numberOfAnalystOpinions"),
                "recommendation_key": info.get("recommendationKey"),
                "recommendation_mean": info.get("recommendationMean"),
                "average_rating": info.get("averageAnalystRating"),
                "current_price": info.get("currentPrice") or info.get("regularMarketPrice"),
            }
        except Exception:
            return {}
    return await asyncio.to_thread(_sync)


async def finnhub_recommendation_trends(symbol: str) -> list[dict]:
    """Returns analyst recommendation buckets per month (strongBuy/buy/hold/sell/strongSell)."""
    url = "https://finnhub.io/api/v1/stock/recommendation"
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(url, params={"symbol": symbol, "token": FINNHUB_KEY})
        if r.status_code != 200:
            return []
        return r.json() or []
