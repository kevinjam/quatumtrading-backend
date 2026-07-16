"""SEC EDGAR XBRL fetcher — stockholders equity + shares outstanding for last 2 quarters."""
import os
from datetime import datetime
from typing import Optional

import httpx

USER_AGENT = os.environ.get("SEC_USER_AGENT", "QuantApp research@example.com")
HEADERS = {"User-Agent": USER_AGENT, "Accept": "application/json"}

_TICKER_MAP_CACHE: Optional[dict] = None


async def _load_ticker_map() -> dict:
    global _TICKER_MAP_CACHE
    if _TICKER_MAP_CACHE is not None:
        return _TICKER_MAP_CACHE
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get("https://www.sec.gov/files/company_tickers.json", headers=HEADERS)
        r.raise_for_status()
        data = r.json()
    _TICKER_MAP_CACHE = {
        v["ticker"].upper(): (str(v["cik_str"]).zfill(10), v["title"])
        for v in data.values()
    }
    return _TICKER_MAP_CACHE


async def get_cik_and_name(ticker: str):
    m = await _load_ticker_map()
    t = ticker.upper()
    if t not in m:
        raise ValueError(f"Ticker {ticker} not found in SEC EDGAR")
    return m[t]


async def _get_concept(cik: str, taxonomy: str, concept: str):
    url = f"https://data.sec.gov/api/xbrl/companyconcept/CIK{cik}/{taxonomy}/{concept}.json"
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(url, headers=HEADERS)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()


def _pick_recent_periodic(facts: dict, unit_key: str, limit: int = 8):
    items = facts.get("units", {}).get(unit_key, [])
    items = [i for i in items if i.get("form") in ("10-Q", "10-K", "10-K/A", "10-Q/A")]
    by_end = {}
    for i in items:
        end = i.get("end")
        if not end:
            continue
        if end not in by_end or i.get("filed", "") > by_end[end].get("filed", ""):
            by_end[end] = i
    return sorted(by_end.values(), key=lambda x: x["end"], reverse=True)[:limit]


async def get_equity_and_shares(ticker: str):
    cik, name = await get_cik_and_name(ticker)

    equity = await _get_concept(cik, "us-gaap", "StockholdersEquity")
    if not equity:
        equity = await _get_concept(
            cik,
            "us-gaap",
            "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
        )
    if not equity:
        raise ValueError(f"No stockholders equity data found for {ticker}")

    eq_items = _pick_recent_periodic(equity, "USD")
    if len(eq_items) < 2:
        raise ValueError(f"Need at least 2 quarters of equity for {ticker}")
    eq_curr, eq_prior = eq_items[0], eq_items[1]

    # Shares — try multiple concepts
    shares = None
    for tax, conc, unit in [
        ("dei", "EntityCommonStockSharesOutstanding", "shares"),
        ("us-gaap", "CommonStockSharesOutstanding", "shares"),
        ("us-gaap", "WeightedAverageNumberOfSharesOutstandingBasic", "shares"),
        ("us-gaap", "WeightedAverageNumberOfDilutedSharesOutstanding", "shares"),
    ]:
        shares = await _get_concept(cik, tax, conc)
        if shares:
            sh_unit_key = unit
            break
    if not shares:
        raise ValueError(f"No shares outstanding data for {ticker}")

    unit_keys = list(shares.get("units", {}).keys())
    sh_unit_key = unit_keys[0] if unit_keys else "shares"
    sh_items = _pick_recent_periodic(shares, sh_unit_key, limit=20)

    def find_for(end_date: str):
        for it in sh_items:
            if it["end"] == end_date:
                return it
        target = datetime.fromisoformat(end_date)
        best, best_diff = None, 10**9
        for it in sh_items:
            try:
                d = datetime.fromisoformat(it["end"])
                diff = abs((d - target).days)
                if diff < best_diff:
                    best_diff = diff
                    best = it
            except Exception:
                pass
        return best

    sh_curr = find_for(eq_curr["end"])
    sh_prior = find_for(eq_prior["end"])
    if not sh_curr or not sh_prior:
        raise ValueError(f"Could not align shares with equity periods for {ticker}")

    return {
        "company_name": name,
        "equity_current": float(eq_curr["val"]),
        "equity_prior": float(eq_prior["val"]),
        "shares_current": float(sh_curr["val"]),
        "shares_prior": float(sh_prior["val"]),
        "quarter_current": f"{eq_curr.get('fp', '')} {eq_curr['end']}",
        "quarter_prior": f"{eq_prior.get('fp', '')} {eq_prior['end']}",
        "filings_used": (
            f"{eq_curr.get('form')} filed {eq_curr.get('filed')} (period {eq_curr['end']}), "
            f"{eq_prior.get('form')} filed {eq_prior.get('filed')} (period {eq_prior['end']})"
        ),
        "is_ipo_no_prior": False,
    }


async def get_recent_10k_text(ticker: str, max_chars: int = 40000) -> str:
    """Pull the most recent 10-K or 10-Q full text to allow LLM to look for going-concern language."""
    cik, _ = await get_cik_and_name(ticker)
    sub_url = f"https://data.sec.gov/submissions/CIK{cik}.json"
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(sub_url, headers=HEADERS)
        r.raise_for_status()
        recent = r.json().get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        access = recent.get("accessionNumber", [])
        docs = recent.get("primaryDocument", [])
        for i, f in enumerate(forms):
            if f in ("10-K", "10-Q"):
                acc = access[i].replace("-", "")
                doc = docs[i]
                url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc}/{doc}"
                d = await c.get(url, headers=HEADERS)
                if d.status_code == 200:
                    return d.text[:max_chars]
                break
    return ""
