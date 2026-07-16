"""Economic calendar — primary source is the Faireconomy / ForexFactory XML feed
(real consensus + previous + actual values, US-focused). Falls back to Claude Sonnet 4.5
if the feed is unreachable or empty.

Note: Investing.com itself is Cloudflare-protected and cannot be scraped from a server
without a paid bypass. Faireconomy.media publishes a free public XML mirror of the same
consensus calendar used by ForexFactory / Investing.com (the JSON endpoint is aggressively
rate-limited; the XML endpoint at .../ff_calendar_thisweek.xml is the production-stable one).
"""
import logging
import os
import re
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx
import json
from emergentintegrations.llm.chat import LlmChat, UserMessage

log = logging.getLogger("economic")

EMERGENT_LLM_KEY = os.environ.get("EMERGENT_LLM_KEY", "")
CACHE_TTL_HOURS = 3
STALE_CACHE_MAX_HOURS = 48
CACHE_KEY = "economic_calendar_14d"

FAIRECONOMY_XML_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.xml"
DEFAULT_COUNTRIES = {"USD"}
ET_TZ = ZoneInfo("America/New_York")

_IMPACT_MAP = {
    "High": "HIGH", "Medium": "MED", "Low": "LOW",
    "HIGH": "HIGH", "MEDIUM": "MED", "LOW": "LOW",
}


def _parse_xml_datetime(date_str: str, time_str: str):
    """Parse ForexFactory XML date/time (e.g. '06-22-2026' / '1:00pm') as US/Eastern."""
    if not date_str:
        return None
    try:
        d = datetime.strptime(date_str.strip(), "%m-%d-%Y").date()
    except Exception:
        return None
    t_str = (time_str or "").strip().lower()
    if not t_str or t_str in ("all day", "tentative"):
        return datetime(d.year, d.month, d.day, 0, 0, tzinfo=ET_TZ)
    try:
        t = datetime.strptime(t_str, "%I:%M%p").time()
    except Exception:
        try:
            t = datetime.strptime(t_str, "%I%p").time()
        except Exception:
            return datetime(d.year, d.month, d.day, 0, 0, tzinfo=ET_TZ)
    return datetime(d.year, d.month, d.day, t.hour, t.minute, tzinfo=ET_TZ)


def _normalize_xml_events(root, countries, window_start, window_end):
    out = []
    for ev in root.findall("event"):
        country = (ev.findtext("country") or "").upper()
        if country not in countries:
            continue
        dt = _parse_xml_datetime(ev.findtext("date") or "", ev.findtext("time") or "")
        if dt is None:
            continue
        dt_utc = dt.astimezone(timezone.utc)
        if not (window_start <= dt_utc <= window_end):
            continue
        impact = _IMPACT_MAP.get((ev.findtext("impact") or "").strip(), "LOW")
        title = (ev.findtext("title") or "").strip() or "Untitled"
        forecast = (ev.findtext("forecast") or "").strip() or None
        previous = (ev.findtext("previous") or "").strip() or None
        actual = (ev.findtext("actual") or "").strip() or None
        time_et = dt.strftime("%H:%M") if (dt.hour or dt.minute) else None
        out.append({
            "date": dt.date().isoformat(),
            "time_et": time_et,
            "country": "US",
            "event": title,
            "impact": impact,
            "forecast": forecast,
            "previous": previous,
            "actual": actual,
        })
    out.sort(key=lambda e: (e["date"], e.get("time_et") or "99:99"))
    return out


async def _fetch_from_faireconomy(window_start, window_end):
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/xml, text/xml, */*",
        "Accept-Language": "en-US,en;q=0.9",
    }
    async with httpx.AsyncClient(timeout=15, headers=headers) as c:
        r = await c.get(FAIRECONOMY_XML_URL)
        if r.status_code == 429:
            raise RuntimeError("faireconomy rate-limited")
        r.raise_for_status()
        text = r.text
    try:
        root = ET.fromstring(text)
    except Exception as e:
        raise RuntimeError(f"faireconomy bad XML: {e}")
    return _normalize_xml_events(root, DEFAULT_COUNTRIES, window_start, window_end)


# ───── LLM FALLBACK ─────
LLM_SYSTEM_PROMPT = """You are a financial-data assistant. Given a date range, return the SCHEDULED
US economic data releases AND major central-bank events for that window. Use your knowledge of
recurring release calendars. DO NOT invent dates.

Rate impact "HIGH" (CPI, NFP, FOMC, PCE, GDP), "MED" (Retail Sales, ISM, Jobless Claims), or "LOW".

Return ONLY a strict JSON object:

{
  "events": [
    {"date": "YYYY-MM-DD", "time_et": "HH:MM" or null, "country": "US",
     "event": "string", "impact": "HIGH | MED | LOW",
     "forecast": "string or null", "previous": "string or null"}
  ]
}

Sort by date then time. Max 25 events."""


def _extract_json(text):
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("No JSON found")
    return json.loads(text[start : end + 1])


async def _fetch_from_llm(start, end):
    user_text = (
        f"Generate the US economic calendar from {start.strftime('%Y-%m-%d')} "
        f"(today, {start.strftime('%A')}) through {end.strftime('%Y-%m-%d')} "
        f"({end.strftime('%A')}). Return strict JSON per schema."
    )
    chat = LlmChat(
        api_key=EMERGENT_LLM_KEY,
        session_id=f"econ-cal-{uuid.uuid4().hex}",
        system_message=LLM_SYSTEM_PROMPT,
    ).with_model("anthropic", "claude-sonnet-4-5-20250929")
    resp = await chat.send_message(UserMessage(text=user_text))
    raw = resp if isinstance(resp, str) else getattr(resp, "content", str(resp))
    parsed = _extract_json(raw)
    out = parsed.get("events", []) or []
    for e in out:
        e["actual"] = None
    return out


async def get_economic_calendar(db):
    """source: 'faireconomy' | 'stale-faireconomy' | 'llm' | 'stale-llm'"""
    now = datetime.now(timezone.utc)
    cached = await db.app_cache.find_one({"key": CACHE_KEY})
    cached_age_h = None
    if cached:
        try:
            cached_at = datetime.fromisoformat(cached["cached_at"].replace("Z", "+00:00"))
            cached_age_h = (now - cached_at).total_seconds() / 3600.0
            if cached_age_h < CACHE_TTL_HOURS:
                return {"source": cached.get("source", "faireconomy"), "events": cached.get("events", [])}
        except Exception:
            cached_age_h = None

    window_start = now
    window_end = now + timedelta(days=14)

    try:
        events = await _fetch_from_faireconomy(window_start, window_end)
        if events:
            await db.app_cache.update_one(
                {"key": CACHE_KEY},
                {"$set": {"key": CACHE_KEY, "events": events, "source": "faireconomy",
                          "cached_at": now.isoformat()}},
                upsert=True,
            )
            return {"source": "faireconomy", "events": events}
        log.warning("faireconomy returned 0 events in window")
    except Exception as e:
        log.warning("faireconomy fetch failed: %s", e)

    if cached and cached_age_h is not None and cached_age_h < STALE_CACHE_MAX_HOURS:
        return {
            "source": "stale-" + cached.get("source", "faireconomy"),
            "events": cached.get("events", []),
        }

    try:
        events = await _fetch_from_llm(window_start, window_end)
    except Exception:
        events = []
    await db.app_cache.update_one(
        {"key": CACHE_KEY},
        {"$set": {"key": CACHE_KEY, "events": events, "source": "llm",
                  "cached_at": now.isoformat()}},
        upsert=True,
    )
    return {"source": "llm", "events": events}


# ═══════════════════════════════════════════════════════════════════
# PULSE — surface the NEXT CPI + ADP release with date/time/forecast.
# ═══════════════════════════════════════════════════════════════════

PULSE_CACHE_KEY = "pulse_cpi_adp"
PULSE_CACHE_TTL_HOURS = 6
PULSE_STALE_MAX_HOURS = 72

# Keywords used to match events from the calendar to our two indicators.
# Order matters — we prefer the most specific match first.
CPI_KEYWORDS = ["cpi y/y", "cpi m/m", "core cpi", "cpi"]
ADP_KEYWORDS = ["adp non-farm employment change", "adp employment", "adp"]


PULSE_LLM_PROMPT = """You are a financial-data assistant. Today is {today} ({weekday}).
Return the NEXT scheduled US release date for these two indicators, based on standard cadence:

  • CPI (Consumer Price Index, monthly, ~mid-month, 8:30am ET)
  • ADP Non-Farm Employment Change (monthly, Wednesday before NFP, 8:15am ET)

Return ONLY strict JSON (no markdown):

{{
  "cpi": {{
    "event": "CPI Y/Y",
    "date": "YYYY-MM-DD",
    "time_et": "HH:MM",
    "forecast": "string or null",
    "previous": "string or null"
  }},
  "adp": {{
    "event": "ADP Non-Farm Employment Change",
    "date": "YYYY-MM-DD",
    "time_et": "HH:MM",
    "forecast": "string or null",
    "previous": "string or null"
  }}
}}

Use the next date strictly AFTER today. Do not invent forecast values — use null if unsure."""


def _match_event(events: list[dict], keywords: list[str]) -> dict | None:
    """Return the EARLIEST event in `events` whose title matches any keyword (case-insensitive)."""
    matches = []
    for ev in events:
        title = (ev.get("event") or "").lower()
        for kw in keywords:
            if kw in title:
                matches.append(ev)
                break
    if not matches:
        return None
    matches.sort(key=lambda e: (e.get("date") or "9999", e.get("time_et") or "99:99"))
    return matches[0]


async def _fetch_pulse_from_llm() -> dict:
    now = datetime.now(timezone.utc)
    prompt = PULSE_LLM_PROMPT.format(
        today=now.strftime("%Y-%m-%d"), weekday=now.strftime("%A")
    )
    chat = LlmChat(
        api_key=EMERGENT_LLM_KEY,
        session_id=f"pulse-{uuid.uuid4().hex}",
        system_message=prompt,
    ).with_model("anthropic", "claude-sonnet-4-5-20250929")
    resp = await chat.send_message(UserMessage(text="Return the next CPI and ADP dates."))
    raw = resp if isinstance(resp, str) else getattr(resp, "content", str(resp))
    parsed = _extract_json(raw)
    out = {}
    for key in ("cpi", "adp"):
        ev = parsed.get(key) or {}
        if not ev.get("date"):
            continue
        out[key] = {
            "event": ev.get("event") or (key.upper() + (" Y/Y" if key == "cpi" else "")),
            "date": ev["date"],
            "time_et": ev.get("time_et"),
            "forecast": ev.get("forecast"),
            "previous": ev.get("previous"),
            "actual": None,
            "country": "US",
            "impact": "HIGH",
        }
    return out


async def get_pulse(db) -> dict:
    """Return next-scheduled CPI + ADP release dates. Prefers Faireconomy data when
    one of the indicators is in this week's feed; otherwise falls back to Claude for
    the next monthly occurrence. Output shape:
      { "source": "faireconomy" | "llm" | "mixed" | "stale-...",
        "cpi":  { event, date, time_et, forecast, previous, actual, impact } | null,
        "adp":  { ... } | null }"""
    now = datetime.now(timezone.utc)
    cached = await db.app_cache.find_one({"key": PULSE_CACHE_KEY})
    cached_age_h = None
    if cached:
        try:
            cached_at = datetime.fromisoformat(cached["cached_at"].replace("Z", "+00:00"))
            cached_age_h = (now - cached_at).total_seconds() / 3600.0
            if cached_age_h < PULSE_CACHE_TTL_HOURS:
                return {
                    "source": cached.get("source", "faireconomy"),
                    "cpi": cached.get("cpi"),
                    "adp": cached.get("adp"),
                }
        except Exception:
            cached_age_h = None

    cpi_ev = None
    adp_ev = None
    feed_ok = False

    # Try Faireconomy this-week first
    try:
        window_start = now
        window_end = now + timedelta(days=14)
        events = await _fetch_from_faireconomy(window_start, window_end)
        feed_ok = True
        cpi_ev = _match_event(events, CPI_KEYWORDS)
        adp_ev = _match_event(events, ADP_KEYWORDS)
    except Exception as e:
        log.warning("pulse: faireconomy fetch failed: %s", e)

    # Fill missing ones via LLM (CPI is monthly so it's often outside this week)
    if cpi_ev is None or adp_ev is None:
        try:
            llm = await _fetch_pulse_from_llm()
            if cpi_ev is None:
                cpi_ev = llm.get("cpi")
            if adp_ev is None:
                adp_ev = llm.get("adp")
        except Exception as e:
            log.warning("pulse: LLM fallback failed: %s", e)

    if cpi_ev is None and adp_ev is None:
        # Total failure — serve stale if recent
        if cached and cached_age_h is not None and cached_age_h < PULSE_STALE_MAX_HOURS:
            return {
                "source": "stale-" + cached.get("source", "faireconomy"),
                "cpi": cached.get("cpi"),
                "adp": cached.get("adp"),
            }

    # Pin impact = HIGH (both are tier-1 macro releases)
    for ev in (cpi_ev, adp_ev):
        if ev is not None:
            ev["impact"] = "HIGH"

    if feed_ok and cpi_ev and adp_ev and cpi_ev.get("forecast") is not None:
        source = "faireconomy"
    elif feed_ok and (cpi_ev or adp_ev):
        source = "mixed"
    else:
        source = "llm"

    payload = {"source": source, "cpi": cpi_ev, "adp": adp_ev}
    await db.app_cache.update_one(
        {"key": PULSE_CACHE_KEY},
        {"$set": {
            "key": PULSE_CACHE_KEY,
            "source": source,
            "cpi": cpi_ev,
            "adp": adp_ev,
            "cached_at": now.isoformat(),
        }},
        upsert=True,
    )
    return payload
