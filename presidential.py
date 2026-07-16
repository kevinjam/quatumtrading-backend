"""Presidential events calendar — LLM-generated upcoming-6-months schedule of the
US President's confirmed/expected public events (summits, signings, major speeches,
debates, foreign trips) with a market-impact rating.

There is no free public API for the US President's schedule. The closest commercial
sources (factba.se, Roll Call) are paywalled. Claude Sonnet 4.5 has strong knowledge
of recurring presidential events (G7, NATO, UN General Assembly, SOTU, Fed cycle,
Davos, APEC, ASEAN, debates, etc.) through its training cutoff, so we use it to
produce a best-effort calendar of likely events. Cached 24h.
"""
import json
import os
import re
import uuid
from datetime import datetime, timezone

from emergentintegrations.llm.chat import LlmChat, UserMessage

EMERGENT_LLM_KEY = os.environ.get("EMERGENT_LLM_KEY", "")
CACHE_KEY = "presidential_events"
CACHE_TTL_HOURS = 24
STALE_CACHE_MAX_HOURS = 168  # 7 days


SYSTEM_PROMPT = """You are a financial-political analyst. Today is {today}.
Return a list of the US President's CONFIRMED or HIGHLY-LIKELY public events from today
through {end_date}. Focus on events the President WILL or LIKELY WILL attend personally.

Include:
- Major international summits: G7, G20, NATO, UN General Assembly (UNGA), APEC, ASEAN, Davos (WEF), BRICS-counterpart meetings, AI summits.
- Bilateral state visits (incoming and outgoing).
- Bill signings of macro-relevant legislation (tax, energy, defense appropriations).
- Federal Reserve appointments / Senate testimony / FOMC-day press events (if president attends).
- State of the Union (Jan/Feb), joint sessions of Congress, major executive orders.
- Campaign-cycle events: presidential debates (Sep/Oct in election years), party conventions (July/Aug election years).
- Press conferences with foreign leaders (e.g. Xi, Putin, Modi, MBS, Zelensky, Netanyahu, Macron, Scholz).
- Significant trade-policy announcements (tariff effective dates, USMCA-style deals).

Do NOT include:
- Routine domestic travel, fundraisers, ribbon-cuttings, holiday photo-ops.

For each event, assess market_impact:
- HIGH = directly market-moving (Fed-related, China/EU trade deal, oil-producer summit, major tariff signing, peace deal announcement, surprise foreign trip).
- MED = sector-moving or sentiment-shifting (G7/G20/NATO when economic agenda is heavy, defense/energy bill signing).
- LOW = mostly symbolic/ceremonial (state visits without policy deliverables, UN speeches, awards).

Return ONLY a strict JSON object — no markdown, no commentary:

{{
  "events": [
    {{
      "date": "YYYY-MM-DD",
      "end_date": "YYYY-MM-DD or null (for multi-day events like G7)",
      "title": "string (concise, e.g. 'G7 Summit · Kananaskis, Canada')",
      "category": "Summit | State Visit | Legislation | Speech | Debate | Trade | Fed | Other",
      "purpose": "1-2 sentence plain-English description of what's expected and why it matters",
      "market_impact": "HIGH | MED | LOW",
      "tickers_to_watch": ["string", ...]  // optional, e.g. ["XLE","DJT","TSLA"] for trade-policy events
    }}
  ]
}}

Sort chronologically. Max 30 events. If unsure of an exact date for a recurring event,
use the standard month/date (e.g. UNGA = 3rd week of September, Davos = Jan 20-24)."""


def _extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("No JSON found")
    return json.loads(text[start : end + 1])


async def _fetch_from_llm(today: datetime, end_date: datetime) -> list[dict]:
    prompt = SYSTEM_PROMPT.format(
        today=today.strftime("%Y-%m-%d (%A)"),
        end_date=end_date.strftime("%Y-%m-%d"),
    )
    chat = LlmChat(
        api_key=EMERGENT_LLM_KEY,
        session_id=f"pres-events-{uuid.uuid4().hex}",
        system_message=prompt,
    ).with_model("anthropic", "claude-sonnet-4-5-20250929")
    resp = await chat.send_message(
        UserMessage(text=f"Return the US presidential events from {today.strftime('%Y-%m-%d')} through end of year per schema.")
    )
    raw = resp if isinstance(resp, str) else getattr(resp, "content", str(resp))
    parsed = _extract_json(raw)
    events = parsed.get("events", []) or []
    # Filter out events strictly before today
    today_str = today.strftime("%Y-%m-%d")
    return [e for e in events if (e.get("date") or "") >= today_str]


async def get_presidential_events(db) -> dict:
    """Returns {source: 'llm'|'stale-llm', events: [...]}.
    Cached 24h with 7-day stale-while-error window."""
    now = datetime.now(timezone.utc)
    cached = await db.app_cache.find_one({"key": CACHE_KEY})
    cached_age_h = None
    if cached:
        try:
            cached_at = datetime.fromisoformat(cached["cached_at"].replace("Z", "+00:00"))
            cached_age_h = (now - cached_at).total_seconds() / 3600.0
            if cached_age_h < CACHE_TTL_HOURS:
                return {"source": cached.get("source", "llm"), "events": cached.get("events", [])}
        except Exception:
            cached_age_h = None

    # End of current year
    end_of_year = datetime(now.year, 12, 31, tzinfo=timezone.utc)
    if (end_of_year - now).days < 30:
        # Within last month of year — extend to mid next year
        end_of_year = datetime(now.year + 1, 6, 30, tzinfo=timezone.utc)

    try:
        events = await _fetch_from_llm(now, end_of_year)
        await db.app_cache.update_one(
            {"key": CACHE_KEY},
            {"$set": {
                "key": CACHE_KEY, "source": "llm", "events": events,
                "cached_at": now.isoformat(),
            }},
            upsert=True,
        )
        return {"source": "llm", "events": events}
    except Exception:
        pass

    # Stale fallback
    if cached and cached_age_h is not None and cached_age_h < STALE_CACHE_MAX_HOURS:
        return {"source": "stale-llm", "events": cached.get("events", [])}

    return {"source": "none", "events": []}
