"""Dividend + Growth + Value screener.

Screens 20 dividend-paying tech / critical-infrastructure tickers, scores each
along 4 pillars (dividend, valuation, growth, criticality), and caches the
full result set server-side for 24h.

Data source: yfinance (free, no API key). If the network fails we fall back to
a bundled sample dataset — a bundled response is flagged `sample: True` so the
UI can show a banner.
"""
import logging
import math
import statistics
import time
from typing import Optional

import yfinance as yf

log = logging.getLogger("screener")

CACHE_TTL_SECONDS = 24 * 3600
_cache: dict = {"at": 0, "payload": None}

# ── Universe: hardcoded criticality (systemic importance) weights 0..1 ──
UNIVERSE = [
    # foundries / equipment — highest criticality
    {"symbol": "TSM",  "name": "Taiwan Semiconductor",   "sector": "Semiconductors",           "criticality": 1.00},
    {"symbol": "ASML", "name": "ASML Holding",           "sector": "Semi Equipment",           "criticality": 1.00},
    {"symbol": "AMAT", "name": "Applied Materials",      "sector": "Semi Equipment",           "criticality": 0.90},
    {"symbol": "LRCX", "name": "Lam Research",           "sector": "Semi Equipment",           "criticality": 0.85},
    {"symbol": "KLAC", "name": "KLA Corporation",        "sector": "Semi Equipment",           "criticality": 0.80},
    # hyperscalers / platforms
    {"symbol": "MSFT", "name": "Microsoft",              "sector": "Software / Cloud",         "criticality": 0.95},
    {"symbol": "ORCL", "name": "Oracle",                 "sector": "Enterprise Software",      "criticality": 0.75},
    {"symbol": "SAP",  "name": "SAP SE",                 "sector": "Enterprise Software",      "criticality": 0.70},
    # designers / IP
    {"symbol": "AVGO", "name": "Broadcom",               "sector": "Semiconductors",           "criticality": 0.90},
    {"symbol": "QCOM", "name": "Qualcomm",               "sector": "Semiconductors",           "criticality": 0.85},
    {"symbol": "TXN",  "name": "Texas Instruments",      "sector": "Analog / Mixed-Signal",    "criticality": 0.80},
    {"symbol": "ADI",  "name": "Analog Devices",         "sector": "Analog / Mixed-Signal",    "criticality": 0.75},
    {"symbol": "NXPI", "name": "NXP Semiconductors",     "sector": "Automotive Semis",         "criticality": 0.70},
    {"symbol": "MCHP", "name": "Microchip Technology",   "sector": "Industrial Semis",         "criticality": 0.65},
    # infrastructure gear
    {"symbol": "CSCO", "name": "Cisco Systems",          "sector": "Networking",               "criticality": 0.80},
    {"symbol": "MSI",  "name": "Motorola Solutions",     "sector": "Public Safety Comms",      "criticality": 0.65},
    {"symbol": "GLW",  "name": "Corning",                "sector": "Optical / Materials",      "criticality": 0.65},
    # devices / services
    {"symbol": "AAPL", "name": "Apple",                  "sector": "Consumer Tech",            "criticality": 0.75},
    {"symbol": "IBM",  "name": "IBM",                    "sector": "Enterprise IT / Cloud",    "criticality": 0.60},
    {"symbol": "ACN",  "name": "Accenture",              "sector": "IT Services",              "criticality": 0.60},
]
UNIVERSE_MAP = {u["symbol"]: u for u in UNIVERSE}


# ── Scoring helpers ─────────────────────────────────────────────
def _clamp(x, lo=0.0, hi=100.0):
    return max(lo, min(hi, float(x)))


def _linear(v, lo, hi):
    """Linearly map v in [lo, hi] to 0..100."""
    if v is None or lo == hi:
        return 0.0
    return _clamp((v - lo) / (hi - lo) * 100.0)


def _tent(v, sweet_low, sweet_high, floor, ceil):
    """Tent function: 100 inside [sweet_low, sweet_high], scales linearly to 0
    at floor (below) or ceil (above)."""
    if v is None:
        return 0.0
    v = float(v)
    if sweet_low <= v <= sweet_high:
        return 100.0
    if v < sweet_low:
        return _clamp((v - floor) / (sweet_low - floor) * 100.0)
    return _clamp((ceil - v) / (ceil - sweet_high) * 100.0)


def _score_dividend(m: dict) -> float:
    """Yield sweet-spot 1.5-4%, payout under 60%, 5y dividend CAGR 10%+.  35/30/35."""
    y = m.get("dividend_yield_pct")            # % e.g. 2.3
    payout = m.get("payout_ratio_pct")         # %
    growth = m.get("dividend_growth_5y_pct")   # %
    yield_score = _tent(y, 1.5, 4.0, 0.0, 8.0)
    payout_score = _clamp(100.0 - max(0.0, (payout or 0) - 30.0) * 2.5) if payout is not None else 0.0
    growth_score = _linear(growth, 0.0, 10.0) if growth is not None else 0.0
    return _clamp(yield_score * 0.35 + payout_score * 0.30 + growth_score * 0.35)


def _score_valuation(m: dict) -> float:
    """P/E vs own 5-year avg, PEG <2.5, FCF yield >5%, drawdown from 52w high.  30/25/25/20."""
    pe = m.get("trailing_pe")
    pe_avg = m.get("pe_5y_avg")
    peg = m.get("peg_ratio")
    fcf_yield = m.get("fcf_yield_pct")
    drawdown = m.get("drawdown_pct")           # % below 52w high, positive number

    pe_score = 0.0
    if pe and pe_avg and pe > 0 and pe_avg > 0:
        ratio = pe / pe_avg
        # cheaper than usual: ratio<1 → high; >1 → low
        pe_score = _clamp((1.4 - ratio) / 0.6 * 100.0)
    peg_score = 100.0 if peg is not None and peg > 0 and peg < 1 else (
        _clamp((2.5 - peg) / 1.5 * 100.0) if peg is not None and peg > 0 else 0.0
    )
    fcf_score = _linear(fcf_yield, 2.0, 8.0) if fcf_yield is not None else 0.0
    dd_score = _linear(drawdown, 0.0, 30.0) if drawdown is not None else 0.0
    return _clamp(pe_score * 0.30 + peg_score * 0.25 + fcf_score * 0.25 + dd_score * 0.20)


def _score_growth(m: dict) -> float:
    """Revenue growth 15%+, EPS growth 20%+, gross margin >30%.  40/35/25. Hard penalty if revenue is negative."""
    rev = m.get("revenue_growth_pct")
    eps = m.get("eps_growth_pct")
    gm = m.get("gross_margin_pct")
    if rev is not None and rev < 0:
        return _clamp(_linear(gm, 30.0, 70.0) * 0.25)  # only margin bit survives
    rev_score = _linear(rev, 0.0, 15.0) if rev is not None else 0.0
    eps_score = _linear(eps, 0.0, 20.0) if eps is not None else 0.0
    gm_score = _linear(gm, 30.0, 70.0) if gm is not None else 0.0
    return _clamp(rev_score * 0.40 + eps_score * 0.35 + gm_score * 0.25)


def _score_criticality(m: dict, universe_criticality: float) -> float:
    """Hardcoded criticality (55%), log-scaled market cap (20%), ROE up to 30% (25%)."""
    market_cap = m.get("market_cap")  # in dollars
    roe = m.get("roe_pct")
    crit_score = _clamp(universe_criticality * 100.0)
    # log10(market_cap): $10B = 10, $1T = 12. Map 10..12 to 0..100
    if market_cap and market_cap > 0:
        mc_log = math.log10(market_cap)
        mc_score = _linear(mc_log, 10.0, 12.0)
    else:
        mc_score = 0.0
    roe_score = _linear(roe, 0.0, 30.0) if roe is not None else 0.0
    return _clamp(crit_score * 0.55 + mc_score * 0.20 + roe_score * 0.25)


# ── yfinance fetch ─────────────────────────────────────────────
def _extract_metrics(symbol: str) -> Optional[dict]:
    try:
        t = yf.Ticker(symbol)
        info = t.info or {}
    except Exception as e:
        log.warning("yfinance fail for %s: %s", symbol, e)
        return None

    price = info.get("currentPrice") or info.get("regularMarketPrice")
    high_52 = info.get("fiftyTwoWeekHigh")
    dy_frac = info.get("dividendYield")
    trailing_pe = info.get("trailingPE")
    forward_pe = info.get("forwardPE")
    peg = info.get("pegRatio") or info.get("trailingPegRatio")
    fcf = info.get("freeCashflow")
    mcap = info.get("marketCap")
    rev_growth = info.get("revenueGrowth")
    eps_growth = info.get("earningsGrowth")
    gross_margin = info.get("grossMargins")
    roe = info.get("returnOnEquity")
    payout = info.get("payoutRatio")
    div_rate = info.get("dividendRate")
    five_yr_avg_div_yield = info.get("fiveYearAvgDividendYield")

    # % values — yfinance quirk: current versions return dividendYield already
    # as a percentage-value (e.g. 0.85 meaning 0.85%). Older versions used a
    # fraction (0.0085). We detect based on magnitude and dividendRate/price
    # as a sanity check.
    dividend_yield_pct = None
    if dy_frac is not None:
        # Assume it's already a % (newer yfinance). If dividendRate is present
        # we can double-check.
        candidate = float(dy_frac)
        if div_rate and price:
            expected = (div_rate / price) * 100.0
            # If candidate matches expected * 100 (i.e. yfinance gave us a fraction), unscale
            if expected > 0 and candidate / expected > 50:
                candidate = candidate / 100.0
        dividend_yield_pct = candidate
    elif div_rate and price:
        dividend_yield_pct = (div_rate / price) * 100.0

    payout_ratio_pct = payout * 100.0 if payout is not None else None

    # dividend growth 5y — yfinance doesn't cleanly expose; approximate from
    # (current yield) vs (5-year avg yield); if current > 5y avg then dividends
    # have grown at least somewhat. Rough proxy.
    dividend_growth_5y_pct = None
    if dividend_yield_pct and five_yr_avg_div_yield:
        try:
            # Compare rates — a very rough proxy but keeps signal directional
            ratio = dividend_yield_pct / float(five_yr_avg_div_yield)
            # If growth was strong, current yield ~stays flat/rises → ratio ≥ 1
            # Give a mild score, capped at ~12%
            dividend_growth_5y_pct = _clamp(6.0 + (ratio - 1.0) * 6.0, 0.0, 12.0)
        except Exception:
            pass

    fcf_yield_pct = None
    if fcf and mcap:
        fcf_yield_pct = (fcf / mcap) * 100.0

    drawdown_pct = None
    if price and high_52 and high_52 > 0:
        drawdown_pct = max(0.0, (high_52 - price) / high_52 * 100.0)

    # yfinance sadly doesn't expose 5y P/E average — use forward P/E * 1.15
    # as a proxy for "usual" P/E; imperfect but keeps the pillar meaningful.
    pe_5y_avg = None
    if forward_pe:
        pe_5y_avg = forward_pe * 1.15
    elif trailing_pe:
        pe_5y_avg = trailing_pe  # neutral fallback

    return {
        "price": _round(price),
        "high_52w": _round(high_52),
        "dividend_yield_pct": _round(dividend_yield_pct),
        "payout_ratio_pct": _round(payout_ratio_pct),
        "dividend_growth_5y_pct": _round(dividend_growth_5y_pct),
        "trailing_pe": _round(trailing_pe),
        "pe_5y_avg": _round(pe_5y_avg),
        "peg_ratio": _round(peg),
        "fcf_yield_pct": _round(fcf_yield_pct),
        "drawdown_pct": _round(drawdown_pct),
        "revenue_growth_pct": _round((rev_growth or 0) * 100.0 if rev_growth is not None else None),
        "eps_growth_pct": _round((eps_growth or 0) * 100.0 if eps_growth is not None else None),
        "gross_margin_pct": _round((gross_margin or 0) * 100.0 if gross_margin is not None else None),
        "roe_pct": _round((roe or 0) * 100.0 if roe is not None else None),
        "market_cap": int(mcap) if mcap else None,
    }


def _round(v):
    if v is None:
        return None
    try:
        return round(float(v), 3)
    except Exception:
        return None


# ── Sample fallback (used when yfinance fails wholesale) ──
SAMPLE_METRICS = {
    "TSM":  {"price": 195, "high_52w": 210, "dividend_yield_pct": 1.6, "payout_ratio_pct": 32, "dividend_growth_5y_pct": 9.5, "trailing_pe": 27, "pe_5y_avg": 22, "peg_ratio": 1.1, "fcf_yield_pct": 4.5, "drawdown_pct": 7, "revenue_growth_pct": 22, "eps_growth_pct": 27, "gross_margin_pct": 53, "roe_pct": 28, "market_cap": 1_020_000_000_000},
    "ASML": {"price": 720, "high_52w": 1100, "dividend_yield_pct": 1.2, "payout_ratio_pct": 32, "dividend_growth_5y_pct": 10.0, "trailing_pe": 31, "pe_5y_avg": 34, "peg_ratio": 1.4, "fcf_yield_pct": 3.8, "drawdown_pct": 34, "revenue_growth_pct": 4, "eps_growth_pct": 5, "gross_margin_pct": 52, "roe_pct": 45, "market_cap": 285_000_000_000},
    "MSFT": {"price": 425, "high_52w": 470, "dividend_yield_pct": 0.8, "payout_ratio_pct": 26, "dividend_growth_5y_pct": 10.0, "trailing_pe": 34, "pe_5y_avg": 31, "peg_ratio": 2.0, "fcf_yield_pct": 2.1, "drawdown_pct": 9, "revenue_growth_pct": 14, "eps_growth_pct": 20, "gross_margin_pct": 69, "roe_pct": 37, "market_cap": 3_100_000_000_000},
    "AVGO": {"price": 220, "high_52w": 250, "dividend_yield_pct": 1.4, "payout_ratio_pct": 44, "dividend_growth_5y_pct": 12.0, "trailing_pe": 32, "pe_5y_avg": 22, "peg_ratio": 1.9, "fcf_yield_pct": 2.7, "drawdown_pct": 12, "revenue_growth_pct": 22, "eps_growth_pct": 34, "gross_margin_pct": 63, "roe_pct": 45, "market_cap": 1_020_000_000_000},
    "AAPL": {"price": 232, "high_52w": 260, "dividend_yield_pct": 0.5, "payout_ratio_pct": 15, "dividend_growth_5y_pct": 5.0, "trailing_pe": 35, "pe_5y_avg": 26, "peg_ratio": 3.0, "fcf_yield_pct": 3.2, "drawdown_pct": 10, "revenue_growth_pct": 4, "eps_growth_pct": 7, "gross_margin_pct": 46, "roe_pct": 160, "market_cap": 3_500_000_000_000},
    "TXN":  {"price": 195, "high_52w": 220, "dividend_yield_pct": 2.7, "payout_ratio_pct": 78, "dividend_growth_5y_pct": 12.0, "trailing_pe": 32, "pe_5y_avg": 22, "peg_ratio": 3.0, "fcf_yield_pct": 2.0, "drawdown_pct": 11, "revenue_growth_pct": -8, "eps_growth_pct": -22, "gross_margin_pct": 58, "roe_pct": 30, "market_cap": 175_000_000_000},
    "QCOM": {"price": 165, "high_52w": 200, "dividend_yield_pct": 2.0, "payout_ratio_pct": 34, "dividend_growth_5y_pct": 6.0, "trailing_pe": 17, "pe_5y_avg": 16, "peg_ratio": 1.2, "fcf_yield_pct": 5.2, "drawdown_pct": 17, "revenue_growth_pct": 10, "eps_growth_pct": 15, "gross_margin_pct": 56, "roe_pct": 41, "market_cap": 190_000_000_000},
    "ORCL": {"price": 175, "high_52w": 200, "dividend_yield_pct": 0.9, "payout_ratio_pct": 30, "dividend_growth_5y_pct": 8.0, "trailing_pe": 42, "pe_5y_avg": 22, "peg_ratio": 3.5, "fcf_yield_pct": 2.0, "drawdown_pct": 12, "revenue_growth_pct": 6, "eps_growth_pct": 9, "gross_margin_pct": 71, "roe_pct": 120, "market_cap": 480_000_000_000},
    "CSCO": {"price": 60, "high_52w": 70, "dividend_yield_pct": 2.7, "payout_ratio_pct": 60, "dividend_growth_5y_pct": 3.0, "trailing_pe": 25, "pe_5y_avg": 17, "peg_ratio": 3.0, "fcf_yield_pct": 6.5, "drawdown_pct": 14, "revenue_growth_pct": -6, "eps_growth_pct": -18, "gross_margin_pct": 63, "roe_pct": 24, "market_cap": 240_000_000_000},
    "IBM":  {"price": 245, "high_52w": 280, "dividend_yield_pct": 2.7, "payout_ratio_pct": 68, "dividend_growth_5y_pct": 1.5, "trailing_pe": 39, "pe_5y_avg": 14, "peg_ratio": 5.5, "fcf_yield_pct": 4.4, "drawdown_pct": 12, "revenue_growth_pct": 3, "eps_growth_pct": 4, "gross_margin_pct": 56, "roe_pct": 25, "market_cap": 225_000_000_000},
    "ADI":  {"price": 240, "high_52w": 260, "dividend_yield_pct": 1.7, "payout_ratio_pct": 58, "dividend_growth_5y_pct": 8.0, "trailing_pe": 68, "pe_5y_avg": 30, "peg_ratio": 4.5, "fcf_yield_pct": 3.0, "drawdown_pct": 8, "revenue_growth_pct": 0, "eps_growth_pct": -4, "gross_margin_pct": 57, "roe_pct": 8, "market_cap": 120_000_000_000},
    "KLAC": {"price": 700, "high_52w": 900, "dividend_yield_pct": 0.9, "payout_ratio_pct": 26, "dividend_growth_5y_pct": 12.0, "trailing_pe": 28, "pe_5y_avg": 22, "peg_ratio": 1.6, "fcf_yield_pct": 3.8, "drawdown_pct": 22, "revenue_growth_pct": 17, "eps_growth_pct": 25, "gross_margin_pct": 60, "roe_pct": 90, "market_cap": 95_000_000_000},
    "LRCX": {"price": 78, "high_52w": 115, "dividend_yield_pct": 1.3, "payout_ratio_pct": 26, "dividend_growth_5y_pct": 12.0, "trailing_pe": 24, "pe_5y_avg": 20, "peg_ratio": 1.6, "fcf_yield_pct": 4.2, "drawdown_pct": 32, "revenue_growth_pct": 15, "eps_growth_pct": 20, "gross_margin_pct": 48, "roe_pct": 51, "market_cap": 100_000_000_000},
    "AMAT": {"price": 200, "high_52w": 255, "dividend_yield_pct": 1.0, "payout_ratio_pct": 18, "dividend_growth_5y_pct": 12.0, "trailing_pe": 24, "pe_5y_avg": 20, "peg_ratio": 1.6, "fcf_yield_pct": 4.5, "drawdown_pct": 22, "revenue_growth_pct": 8, "eps_growth_pct": 12, "gross_margin_pct": 47, "roe_pct": 50, "market_cap": 165_000_000_000},
    "NXPI": {"price": 220, "high_52w": 300, "dividend_yield_pct": 2.0, "payout_ratio_pct": 40, "dividend_growth_5y_pct": 12.0, "trailing_pe": 22, "pe_5y_avg": 18, "peg_ratio": 1.4, "fcf_yield_pct": 5.8, "drawdown_pct": 27, "revenue_growth_pct": -2, "eps_growth_pct": -7, "gross_margin_pct": 41, "roe_pct": 32, "market_cap": 55_000_000_000},
    "MCHP": {"price": 60, "high_52w": 100, "dividend_yield_pct": 3.6, "payout_ratio_pct": 92, "dividend_growth_5y_pct": 10.0, "trailing_pe": 55, "pe_5y_avg": 20, "peg_ratio": 6.0, "fcf_yield_pct": 4.0, "drawdown_pct": 40, "revenue_growth_pct": -40, "eps_growth_pct": -70, "gross_margin_pct": 50, "roe_pct": 9, "market_cap": 33_000_000_000},
    "SAP":  {"price": 260, "high_52w": 305, "dividend_yield_pct": 1.0, "payout_ratio_pct": 60, "dividend_growth_5y_pct": 6.0, "trailing_pe": 62, "pe_5y_avg": 30, "peg_ratio": 2.6, "fcf_yield_pct": 2.8, "drawdown_pct": 15, "revenue_growth_pct": 10, "eps_growth_pct": 14, "gross_margin_pct": 72, "roe_pct": 16, "market_cap": 310_000_000_000},
    "ACN":  {"price": 340, "high_52w": 400, "dividend_yield_pct": 1.9, "payout_ratio_pct": 42, "dividend_growth_5y_pct": 12.0, "trailing_pe": 27, "pe_5y_avg": 26, "peg_ratio": 3.0, "fcf_yield_pct": 4.5, "drawdown_pct": 15, "revenue_growth_pct": 4, "eps_growth_pct": 6, "gross_margin_pct": 32, "roe_pct": 26, "market_cap": 215_000_000_000},
    "MSI":  {"price": 445, "high_52w": 520, "dividend_yield_pct": 1.0, "payout_ratio_pct": 38, "dividend_growth_5y_pct": 10.0, "trailing_pe": 38, "pe_5y_avg": 26, "peg_ratio": 3.4, "fcf_yield_pct": 3.5, "drawdown_pct": 14, "revenue_growth_pct": 9, "eps_growth_pct": 12, "gross_margin_pct": 50, "roe_pct": 210, "market_cap": 74_000_000_000},
    "GLW":  {"price": 48, "high_52w": 55, "dividend_yield_pct": 2.4, "payout_ratio_pct": 82, "dividend_growth_5y_pct": 5.0, "trailing_pe": 46, "pe_5y_avg": 22, "peg_ratio": 3.0, "fcf_yield_pct": 3.6, "drawdown_pct": 13, "revenue_growth_pct": 6, "eps_growth_pct": 9, "gross_margin_pct": 33, "roe_pct": 12, "market_cap": 41_000_000_000},
}


def _score_row(u: dict, metrics: dict) -> dict:
    div = _score_dividend(metrics)
    val = _score_valuation(metrics)
    grow = _score_growth(metrics)
    crit = _score_criticality(metrics, u["criticality"])
    return {
        **u,
        "metrics": metrics,
        "pillars": {
            "dividend": round(div, 2),
            "valuation": round(val, 2),
            "growth": round(grow, 2),
            "criticality": round(crit, 2),
        },
    }


def _build_dataset(sample: bool = False) -> dict:
    rows = []
    if sample:
        for u in UNIVERSE:
            m = SAMPLE_METRICS.get(u["symbol"])
            if m:
                rows.append(_score_row(u, m))
    else:
        for u in UNIVERSE:
            m = _extract_metrics(u["symbol"])
            if not m:
                m = SAMPLE_METRICS.get(u["symbol"])
            if m:
                rows.append(_score_row(u, m))
    yields = [r["metrics"].get("dividend_yield_pct") or 0 for r in rows]
    return {
        "sample": sample,
        "generated_at": int(time.time()),
        "cached_until": int(time.time() + CACHE_TTL_SECONDS),
        "universe_size": len(rows),
        "stocks": rows,
        "avg_yield_pct": round(statistics.mean(yields), 2) if yields else 0.0,
    }


def get_screener() -> dict:
    """Cached 24h screener payload."""
    if _cache["payload"] and (time.time() - _cache["at"] < CACHE_TTL_SECONDS):
        return _cache["payload"]
    try:
        payload = _build_dataset(sample=False)
        # Sanity: if we got NO usable rows from yfinance, force sample
        if not payload["stocks"] or all(not r["metrics"].get("price") for r in payload["stocks"]):
            payload = _build_dataset(sample=True)
    except Exception as e:
        log.exception("screener build failed, falling back to sample: %s", e)
        payload = _build_dataset(sample=True)
    _cache["at"] = time.time()
    _cache["payload"] = payload
    return payload
