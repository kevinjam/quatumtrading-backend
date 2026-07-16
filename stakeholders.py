"""Stakeholder discovery: trillion-dollar institutions + 100B+ billionaires.

Strategy:
  1. yfinance institutional_holders → filter against a hard-coded list of >$1T-AUM firms.
  2. LLM (Claude Sonnet 4.5) identifies any individuals with 100B+ net worth who own a
     meaningful stake (typically founders/CEOs like Jensen Huang at NVDA, Bezos at AMZN,
     Musk at TSLA, Buffett-via-Berkshire at AAPL, Trump at DJT, etc.).
"""
import json
import os
import re
import uuid
from typing import List

import yfinance as yf
from emergentintegrations.llm.chat import LlmChat, UserMessage

EMERGENT_LLM_KEY = os.environ.get("EMERGENT_LLM_KEY", "")

# Substring matches against yfinance "Holder" strings → considered trillion-$ AUM firms.
TRILLION_FIRM_KEYWORDS = [
    "vanguard", "blackrock", "state street", "fmr", "fidelity",
    "geode", "jpmorgan", "jp morgan", "morgan stanley", "goldman sachs",
    "capital group", "t. rowe", "price (t.rowe)", "price t. rowe",
    "wellington", "bank of america", "bofa", "northern trust",
    "ubs", "amundi", "allianz", "pimco", "invesco",
    "berkshire hathaway",
]


def get_top_institutional(ticker: str) -> List[dict]:
    try:
        t = yf.Ticker(ticker)
        df = t.institutional_holders
        if df is None or df.empty:
            return []
        out = []
        for _, row in df.head(15).iterrows():
            holder = str(row.get("Holder", "")).strip()
            try:
                pct = float(row.get("pctHeld") or 0) * 100
            except Exception:
                pct = 0
            try:
                value = float(row.get("Value") or 0)
            except Exception:
                value = 0
            try:
                shares = int(row.get("Shares") or 0)
            except Exception:
                shares = 0
            out.append({
                "holder": holder,
                "pct_held": round(pct, 2),
                "value_usd": value,
                "shares": shares,
            })
        return out
    except Exception:
        return []


def filter_trillion(holders: List[dict]) -> List[dict]:
    out = []
    for h in holders:
        name = h["holder"].lower()
        if any(k in name for k in TRILLION_FIRM_KEYWORDS):
            out.append(h)
    return out


BILLIONAIRE_SYSTEM = """You are a finance research assistant. Given a public company (ticker + name),
list any INDIVIDUALS (real people) currently estimated to have a net worth ≥ $100 BILLION USD who hold
a material direct or beneficial stake in this company. Focus on founders, CEOs, mega-investors.

Examples you should recognize:
- AAPL → Warren Buffett (via Berkshire Hathaway holding ~5-6%)
- NVDA → Jensen Huang (founder/CEO, ~3-4%)
- TSLA → Elon Musk (~13%)
- AMZN → Jeff Bezos (~9%)
- META → Mark Zuckerberg (~13% voting)
- MSFT → Steve Ballmer (~4%), Bill Gates (small via Cascade)
- GOOGL/GOOG → Larry Page, Sergey Brin (founders, super-voting)
- ORCL → Larry Ellison (~40%)
- BRK.A/B → Warren Buffett
- DJT → Donald Trump (controlling stake)
- LVMH → Bernard Arnault
- DELL → Michael Dell

ONLY include people whose NET WORTH is ≥ $100B as of the most recent estimates you know.
If none qualify, return an empty array.

Return STRICT JSON only (no markdown, no commentary):
{
  "billionaires": [
    {"name": "string", "role": "string — e.g. 'Founder & CEO', 'Chairman via Berkshire'", "net_worth_b": number, "stake_pct": number_or_null, "note": "1-sentence context"}
  ]
}"""


def _extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    s = text.find("{"); e = text.rfind("}")
    if s == -1 or e == -1:
        return {"billionaires": []}
    try:
        return json.loads(text[s : e + 1])
    except Exception:
        return {"billionaires": []}


async def find_billionaire_stakeholders(ticker: str, company: str) -> List[dict]:
    chat = LlmChat(
        api_key=EMERGENT_LLM_KEY,
        session_id=f"billi-{uuid.uuid4().hex}",
        system_message=BILLIONAIRE_SYSTEM,
    ).with_model("anthropic", "claude-sonnet-4-5-20250929")
    resp = await chat.send_message(
        UserMessage(text=json.dumps({"ticker": ticker, "company": company}))
    )
    raw = resp if isinstance(resp, str) else getattr(resp, "content", str(resp))
    data = _extract_json(raw)
    return data.get("billionaires", []) or []


async def get_stakeholders(ticker: str, company: str) -> dict:
    holders = get_top_institutional(ticker)
    trillion = filter_trillion(holders)
    try:
        billionaires = await find_billionaire_stakeholders(ticker, company)
    except Exception:
        billionaires = []
    return {
        "trillion_dollar_firms": trillion,
        "billionaires": billionaires,
        "total_institutional_pct": sum(h["pct_held"] for h in holders),
    }
