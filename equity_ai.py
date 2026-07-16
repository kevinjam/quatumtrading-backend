"""Claude fallback for stockholders equity + shares outstanding when SEC EDGAR is missing or insufficient."""
import json
import os
import re
import uuid

from emergentintegrations.llm.chat import LlmChat, UserMessage

EMERGENT_LLM_KEY = os.environ.get("EMERGENT_LLM_KEY", "")

SYSTEM_PROMPT = """You are a financial-data research assistant. Given a stock ticker and company name,
return your BEST ESTIMATE of the company's total stockholders equity and shares outstanding for the
two most recent reported quarters (10-Q / 10-K).

Rules:
- Use the most recent values you know from public filings (10-Q, 10-K, earnings releases).
- Equity is in USD (absolute dollars, NOT millions).
- Shares is the absolute share count (NOT millions).
- Quarter labels look like "Q1 2026", "Q2 2025", "FY 2025", etc.
- If the company reports in a non-USD currency, convert to USD using a reasonable recent rate.
- If you genuinely don't know, set fields to null and explain in `note`.

Return STRICT JSON only (no markdown, no backticks):
{
  "company_name": "string",
  "equity_current": number_or_null,
  "equity_prior": number_or_null,
  "shares_current": number_or_null,
  "shares_prior": number_or_null,
  "quarter_current": "string",
  "quarter_prior": "string",
  "note": "string — source / period / confidence"
}"""


def _extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    s = text.find("{"); e = text.rfind("}")
    if s == -1 or e == -1:
        raise ValueError("No JSON found in equity estimate response")
    return json.loads(text[s : e + 1])


async def ai_estimate_equity_shares(ticker: str, company: str = "") -> dict:
    chat = LlmChat(
        api_key=EMERGENT_LLM_KEY,
        session_id=f"equity-{uuid.uuid4().hex}",
        system_message=SYSTEM_PROMPT,
    ).with_model("anthropic", "claude-sonnet-4-5-20250929")
    resp = await chat.send_message(
        UserMessage(text=json.dumps({"ticker": ticker, "company": company}))
    )
    raw = resp if isinstance(resp, str) else getattr(resp, "content", str(resp))
    data = _extract_json(raw)

    # Validate
    for k in ("equity_current", "equity_prior", "shares_current", "shares_prior"):
        v = data.get(k)
        if v is None or (isinstance(v, (int, float)) and v == 0):
            raise ValueError(f"AI estimate incomplete: {k} missing for {ticker}")

    return {
        "company_name": data.get("company_name") or company or ticker,
        "equity_current": float(data["equity_current"]),
        "equity_prior": float(data["equity_prior"]),
        "shares_current": float(data["shares_current"]),
        "shares_prior": float(data["shares_prior"]),
        "quarter_current": data.get("quarter_current", "AI estimate"),
        "quarter_prior": data.get("quarter_prior", "AI estimate"),
        "filings_used": f"AI estimate ({data.get('note', 'Claude Sonnet 4.5')})",
        "is_ipo_no_prior": False,
        "ai_estimated": True,
    }
