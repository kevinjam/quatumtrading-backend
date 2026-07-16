"""Look up notable stock holdings for a named person (CEO, President, billionaire, etc.) via Claude.

For the current sitting U.S. President, a hard-coded categorized portfolio is returned (based on
OGE Form 278e disclosures + publicly reported holdings) so the result is consistent across calls.
"""
import json
import os
import re
import uuid

from emergentintegrations.llm.chat import LlmChat, UserMessage

EMERGENT_LLM_KEY = os.environ.get("EMERGENT_LLM_KEY", "")


# Hard-coded authoritative portfolio for the current sitting U.S. President.
TRUMP_PORTFOLIO = {
    "person": "Donald Trump",
    "role": "President of the United States",
    "summary": (
        "Donald Trump's most-recent OGE Form 278e disclosure plus publicly reported holdings span "
        "technology, defense, finance and consumer staples — in addition to his controlling stake in "
        "DJT (Trump Media & Technology Group)."
    ),
    "categories": [
        {
            "name": "Technology & AI",
            "holdings": [
                {"ticker": "AAPL", "company": "Apple Inc.", "note": "Mega-cap tech holding"},
                {"ticker": "NVDA", "company": "Nvidia Corp.", "note": "GPU / AI infrastructure"},
                {"ticker": "MSFT", "company": "Microsoft Corp.", "note": "Cloud + AI platform"},
                {"ticker": "ORCL", "company": "Oracle Corp.", "note": "Enterprise cloud + DB"},
                {"ticker": "DELL", "company": "Dell Technologies Inc.", "note": "Hardware / AI servers"},
                {"ticker": "AVGO", "company": "Broadcom Inc.", "note": "Semiconductors + VMware"},
            ],
        },
        {
            "name": "Defense & Aerospace",
            "holdings": [
                {"ticker": "BA", "company": "The Boeing Company", "note": "Commercial + defense aviation"},
                {"ticker": "LMT", "company": "Lockheed Martin", "note": "Prime defense contractor"},
                {"ticker": "NOC", "company": "Northrop Grumman", "note": "Aerospace + missile systems"},
                {"ticker": "PLTR", "company": "Palantir Technologies", "note": "AI-defense data platform"},
            ],
        },
        {
            "name": "Finance",
            "holdings": [
                {"ticker": "OBDC", "company": "Blue Owl Capital Corp.", "note": "BDC / direct lending"},
            ],
        },
        {
            "name": "Retail & Consumer",
            "holdings": [
                {"ticker": "COST", "company": "Costco Wholesale", "note": "Membership retail"},
                {"ticker": "WMT", "company": "Walmart Inc.", "note": "Mass-market retail"},
                {"ticker": "KO", "company": "The Coca-Cola Company", "note": "Beverages"},
            ],
        },
        {
            "name": "Media",
            "holdings": [
                {
                    "ticker": "DJT",
                    "company": "Trump Media & Technology Group",
                    "stake_pct": 57,
                    "note": "Controlling stake — Truth Social parent",
                },
            ],
        },
    ],
}


def _flatten(categories):
    """Flatten categories[].holdings[] into a single list for backwards compatibility."""
    out = []
    for cat in categories:
        for h in cat.get("holdings", []):
            out.append({**h, "category": cat.get("name", "Other")})
    return out


SYSTEM_PROMPT = """You are a finance research assistant. The input may be EITHER a person OR a company.

Determine the input type by the `role` field:
- "President" / "CEO" / blank → treat `name` as a PERSON. Return that person's most significant publicly-known stock holdings.
- "Corp" / "Company" / "Corporate" → treat `name` as a COMPANY. Return that company's strategic equity investments and stakes in OTHER public companies (e.g. NVIDIA → ARM/RXRX/SOUN/NNOX/Cohere; Berkshire → AAPL/KO/AXP; Microsoft → OpenAI; Alphabet's portfolio; Saudi Aramco; Tencent's gaming portfolio).

Rules:
- ALWAYS group the holdings into 2-5 sensible CATEGORIES such as "Technology & AI", "Defense & Aerospace", "Finance", "Retail & Consumer", "Media", "Energy", "Healthcare", etc.
- Each category has up to 8 holdings, sorted by approximate dollar value or strategic significance.
- For corporations, focus on equity stakes in OTHER companies — do NOT return the company's own ticker.
- Use the standard US/Global stock ticker symbol when known.
- If you don't recognize the input or have no notable holdings, return an empty categories list.

Return STRICT JSON only (no markdown, no backticks):
{
  "person": "string",
  "role": "string",
  "summary": "string — 1-2 sentence context",
  "categories": [
    {
      "name": "string — category label",
      "holdings": [
        {
          "ticker": "string — public ticker symbol, or '' if private",
          "company": "string",
          "stake_pct": number_or_null,
          "value_usd_billions": number_or_null,
          "note": "1-sentence context"
        }
      ]
    }
  ]
}"""


def _extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    s = text.find("{"); e = text.rfind("}")
    if s == -1 or e == -1:
        return {"categories": []}
    try:
        return json.loads(text[s : e + 1])
    except Exception:
        return {"categories": []}


def _is_current_president(name: str, role: str) -> bool:
    n = name.lower().strip()
    r = role.lower().strip()
    if "trump" not in n:
        return False
    # Require either explicit "president" role OR the full "donald trump" name
    return ("president" in r) or ("donald" in n)


async def lookup_person_holdings(name: str, role: str = "") -> dict:
    # Authoritative hard-coded response for the sitting U.S. President.
    if _is_current_president(name, role):
        data = json.loads(json.dumps(TRUMP_PORTFOLIO))  # deep copy
        data["holdings"] = _flatten(data["categories"])
        return data

    chat = LlmChat(
        api_key=EMERGENT_LLM_KEY,
        session_id=f"people-{uuid.uuid4().hex}",
        system_message=SYSTEM_PROMPT,
    ).with_model("anthropic", "claude-sonnet-4-5-20250929")
    resp = await chat.send_message(
        UserMessage(text=json.dumps({"name": name.strip(), "role": role.strip()}))
    )
    raw = resp if isinstance(resp, str) else getattr(resp, "content", str(resp))
    data = _extract_json(raw)

    data.setdefault("person", name)
    data.setdefault("role", role)
    data.setdefault("summary", "")
    data.setdefault("categories", [])

    # Back-compat: also provide flat holdings list with `category` field.
    if data["categories"]:
        data["holdings"] = _flatten(data["categories"])
    else:
        # Old shape — promote holdings[] into a single "Other" category
        flat = data.get("holdings", []) or []
        if flat:
            data["categories"] = [{"name": "Other", "holdings": flat}]
            data["holdings"] = _flatten(data["categories"])
        else:
            data["holdings"] = []
    return data

