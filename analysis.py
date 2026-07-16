"""Orchestrator: deterministic math for V1, V2, V3, V4 + LLM for V5/V6/distortion/going-concern."""
from datetime import datetime, timezone

from edgar import get_equity_and_shares, get_recent_10k_text
from equity_ai import ai_estimate_equity_shares
from market import (
    finnhub_insider_transactions,
    finnhub_institutional,
    get_next_earnings_date,
    get_price_data,
)
from llm import analyze_meta
from stakeholders import get_stakeholders


def _v1_signal(value: float) -> str:
    if value >= 200:
        return "Strong Bull"
    if value >= 100:
        return "Bull"
    if value >= 90:
        return "Moderately Positive"
    if value >= 60:
        return "Bear"
    return "Strong Bear"


def _v3_band(score: float) -> str:
    if score > 20:
        return "Strong Long"
    if score >= 5:
        return "Watch"
    if score >= -5:
        return "Skip"
    if score >= -20:
        return "Bear Watch"
    return "Confirmed Bear"


async def run_full_analysis(ticker: str, manual_price: dict | None = None) -> dict:
    ticker = ticker.upper().strip()

    # 1. Price (manual override > live fetch)
    if manual_price:
        price = {
            "high_52w": float(manual_price["high_52w"]),
            "low_52w": float(manual_price["low_52w"]),
            "current": float(manual_price["current"]),
            "company_name": manual_price.get("company_name"),
            "price_source": "manual entry",
        }
    else:
        price = await get_price_data(ticker)

    # 2. SEC — fall back to Claude AI estimate if EDGAR fails or lacks 2 quarters
    sec_source = "SEC EDGAR"
    try:
        sec = await get_equity_and_shares(ticker)
    except Exception as sec_err:
        try:
            sec = await ai_estimate_equity_shares(ticker, price.get("company_name") or "")
            sec_source = "AI estimate"
        except Exception as ai_err:
            if not manual_price:
                raise ValueError(
                    f"Equity/shares data unavailable for {ticker} "
                    f"(SEC: {sec_err}; AI fallback: {ai_err})"
                )
            sec = {
                "company_name": price.get("company_name") or ticker,
                "equity_current": 0.0,
                "equity_prior": 0.0,
                "shares_current": 1.0,
                "shares_prior": 1.0,
                "quarter_current": "N/A",
                "quarter_prior": "N/A",
                "filings_used": f"No SEC EDGAR / AI coverage — V2/V3 inert",
                "is_ipo_no_prior": False,
            }
            sec_source = "stub (no data)"
    company = price.get("company_name") or sec["company_name"]

    # 3. V1
    high, low, curr = price["high_52w"], price["low_52w"], price["current"]
    v1_value = (high - low) / curr * 100 if curr else 0.0
    position_pct = (curr - low) / (high - low) * 100 if (high - low) else 0.0
    v1 = {
        "value": round(v1_value, 2),
        "signal": _v1_signal(v1_value),
        "position_pct": round(position_pct, 2),
    }

    # 4. V2
    eq_curr, eq_prior = sec["equity_current"], sec["equity_prior"]
    sh_curr, sh_prior = sec["shares_current"], sec["shares_prior"]
    bv_curr = eq_curr / sh_curr if sh_curr else 0.0
    bv_prior = eq_prior / sh_prior if sh_prior else 0.0
    if bv_prior == 0:
        v2_value = 0.0
    else:
        v2_value = (bv_curr - bv_prior) / abs(bv_prior) * 100
    direction = "inflow" if v2_value > 0.5 else ("outflow" if v2_value < -0.5 else "flatline")

    v2 = {
        "value": round(v2_value, 2),
        "direction": direction,
        "override_fires": abs(v2_value) > 10,
        "distortion_type": None,
        "distortion_note": None,
        "bv_current": round(bv_curr, 4),
        "bv_prior": round(bv_prior, 4),
        "equity_current_millions": round(eq_curr / 1e6, 2),
        "equity_prior_millions": round(eq_prior / 1e6, 2),
        "shares_current_millions": round(sh_curr / 1e6, 2),
        "shares_prior_millions": round(sh_prior / 1e6, 2),
        "quarter_current": sec["quarter_current"],
        "quarter_prior": sec["quarter_prior"],
    }

    # 5. V3
    score = v1_value * v2_value / 100
    override_active = abs(v2_value) > 10
    if override_active:
        direction_overall = "Bull" if v2_value > 0 else "Bear"
    else:
        direction_overall = "Bull" if score >= 0 else "Bear"

    v3 = {
        "score": round(score, 2),
        "signal": _v3_band(score),
        "direction": direction_overall,
        "override_active": override_active,
        "invalid": False,
        "invalid_reason": None,
    }

    # 6. V4 — institutional + insider
    inst = await finnhub_institutional(ticker)
    insd = await finnhub_insider_transactions(ticker)

    def _overall(a: str, b: str) -> str:
        if "Negative" in (a, b):
            return "Negative"
        if a == "Pending" and b == "Pending":
            return "Pending"
        return "Positive"

    v4 = {
        "v4a_ratio": round(inst["ratio"], 2) if inst.get("ratio") is not None else None,
        "v4b_ratio": round(insd["ratio"], 2) if insd.get("ratio") is not None else None,
        "v4a_inflows": inst.get("inflows"),
        "v4a_outflows": inst.get("outflows"),
        "v4a_signal": inst["signal"],
        "v4b_signal": insd["signal"],
        "overall": _overall(inst["signal"], insd["signal"]),
        "notes": f"{inst.get('note', '')} | {insd.get('note', '')}",
    }

    # 6.5. Cash / Debt ratio (via yfinance .info — quick lookup)
    cash_debt = {"cash": None, "debt": None, "ratio": None}
    try:
        import yfinance as yf
        info = getattr(yf.Ticker(ticker), "info", {}) or {}
        cash_val = info.get("totalCash")
        debt_val = info.get("totalDebt")
        if cash_val is not None and debt_val is not None and float(debt_val) > 0:
            cash_debt = {
                "cash": float(cash_val),
                "debt": float(debt_val),
                "ratio": round(float(cash_val) / float(debt_val), 4),
            }
    except Exception:
        pass

    # 7. earnings
    earnings_date = await get_next_earnings_date(ticker)
    days_to_earnings = None
    if earnings_date:
        try:
            d = datetime.fromisoformat(earnings_date)
            days_to_earnings = (d.date() - datetime.now(timezone.utc).date()).days
        except Exception:
            pass
    v6_applicable = days_to_earnings is not None and 0 <= days_to_earnings <= 14

    # 8. SEC excerpt for LLM going-concern scan
    try:
        filing_excerpt = await get_recent_10k_text(ticker, max_chars=25000)
    except Exception:
        filing_excerpt = ""

    # 9. LLM meta layer
    llm_payload = {
        "ticker": ticker,
        "company": company,
        "v1": v1,
        "v2": v2,
        "v3": v3,
        "v4": v4,
        "earnings_date": earnings_date,
        "days_to_earnings": days_to_earnings,
        "v6_applicable": v6_applicable,
    }
    try:
        llm_out = await analyze_meta(llm_payload, filing_excerpt)
    except Exception as e:
        llm_out = {
            "v2_distortion_type": None,
            "v2_distortion_note": f"LLM unavailable: {e}",
            "v3_invalid": False,
            "v3_invalid_reason": None,
            "v5_rating": "AI Neutral",
            "v5_signal": "Neutral",
            "v5_reasoning": f"LLM analysis failed: {e}",
            "v6_decision": "N/A",
            "v6_criteria_met": "LLM analysis failed",
            "v6_reasoning": "Unable to compute V6 due to LLM error.",
            "going_concern": False,
            "going_concern_note": None,
            "final_signal": "Skip",
            "final_reasoning": "LLM layer failed; defer to quantitative outputs only.",
        }

    # Merge LLM output back
    v2["distortion_type"] = llm_out.get("v2_distortion_type")
    v2["distortion_note"] = llm_out.get("v2_distortion_note")
    v3["invalid"] = bool(llm_out.get("v3_invalid"))
    v3["invalid_reason"] = llm_out.get("v3_invalid_reason")

    v5 = {
        "rating": llm_out.get("v5_rating", "AI Neutral"),
        "signal": llm_out.get("v5_signal", "Neutral"),
        "reasoning": llm_out.get("v5_reasoning", ""),
    }

    v6 = {
        "applicable": v6_applicable,
        "decision": llm_out.get("v6_decision", "N/A") if v6_applicable else "N/A",
        "earnings_date": earnings_date,
        "criteria_met": llm_out.get("v6_criteria_met", "Not within 14 days") if v6_applicable else "Earnings not within 14 days",
        "reasoning": llm_out.get("v6_reasoning", "") if v6_applicable else "V6 only applies if earnings are within 14 days.",
    }

    going_concern = bool(llm_out.get("going_concern", False))
    going_concern_note = llm_out.get("going_concern_note")

    # Final signal — soft hierarchy: keep the V3 band as the headline label and
    # surface overrides via the V4 / going_concern flags on the cards.
    if v3["invalid"]:
        natural = v3["signal"]
    else:
        natural = llm_out.get("final_signal") or v3["signal"]
    mapping = {
        "Strong Long": "Strong Long",
        "Watch": "Watch",
        "Skip": "Skip",
        "Bear Watch": "Bear Watch",
        "Confirmed Bear": "Bear",
        "Do Not Enter": "Bear",
        "Caution": "Bear",
        "Invalid": "Skip",
    }
    final_signal = mapping.get(natural, natural)

    result = {
        "ticker": ticker,
        "company": company,
        "price_data": {
            "high_52w": high,
            "low_52w": low,
            "current": curr,
            "position_pct": round(position_pct, 2),
        },
        "stakeholders": await get_stakeholders(ticker, company),
        "v1": v1,
        "v2": v2,
        "v3": v3,
        "v4": v4,
        "v5": v5,
        "v6": v6,
        "going_concern": going_concern,
        "going_concern_note": going_concern_note,
        "final_signal": final_signal,
        "final_reasoning": llm_out.get("final_reasoning", ""),
        "cash_debt": cash_debt,
        "data_sources": (
            f"Equity/Shares ({sec_source}): {sec['filings_used']} | Price: {price.get('price_source', 'Finnhub')} "
            f"(current may be last-close estimate) | "
            f"Inst/Insider: Finnhub | Meta: Claude Sonnet 4.5"
        ),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    return result
