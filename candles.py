"""yfinance candles + Claude pattern + trade-plan analysis.

The AI is fed pre-computed technical indicators (RSI 14, MACD 12/26/9, Bollinger
Bands 20/2) along with the most recent candles. If the user has highlighted a
section of the chart on the frontend, we pass that highlighted slice separately
so the LLM focuses its support/resistance reasoning on that region.

Response includes multiple support/resistance levels (each labeled bullish or
bearish + strength) so the frontend can draw them as color-coded price lines.
"""
import json
import os
import re
import uuid
from typing import List, Optional

import pandas as pd
import yfinance as yf
from emergentintegrations.llm.chat import LlmChat, UserMessage

EMERGENT_LLM_KEY = os.environ.get("EMERGENT_LLM_KEY", "")

# Map UI interval → yfinance (interval, period)
INTERVAL_MAP = {
    "5m": ("5m", "5d"),
    "1h": ("60m", "60d"),
    "1d": ("1d", "1y"),
    "1w": ("1wk", "5y"),
}


def fetch_candles(symbol: str, interval: str) -> List[dict]:
    if interval not in INTERVAL_MAP:
        raise ValueError(f"Unsupported interval {interval}")
    yf_interval, period = INTERVAL_MAP[interval]
    t = yf.Ticker(symbol)
    df = t.history(period=period, interval=yf_interval, auto_adjust=False)
    if df.empty:
        return []
    df = df.reset_index()
    time_col = "Datetime" if "Datetime" in df.columns else "Date"
    out = []
    for _, row in df.iterrows():
        try:
            ts = row[time_col]
            unix = int(ts.timestamp())
            out.append({
                "time": unix,
                "open": round(float(row["Open"]), 4),
                "high": round(float(row["High"]), 4),
                "low": round(float(row["Low"]), 4),
                "close": round(float(row["Close"]), 4),
                "volume": int(row["Volume"]) if row["Volume"] == row["Volume"] else 0,
            })
        except Exception:
            continue
    return out


# ─────────────────────────────── Indicators ───────────────────────────────
def _compute_indicators(candles: List[dict]) -> dict:
    """Compute the latest RSI, MACD, Bollinger Bands from the closes series."""
    if len(candles) < 30:
        return {}
    df = pd.DataFrame(candles)
    close = df["close"].astype(float)

    # RSI (14, Wilder)
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    rs = gain / loss.replace(0, 1e-9)
    rsi = 100 - (100 / (1 + rs))

    # MACD (12, 26, 9)
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd_line = ema12 - ema26
    signal_line = macd_line.ewm(span=9, adjust=False).mean()
    hist = macd_line - signal_line

    # Bollinger Bands (20, 2)
    mid = close.rolling(20).mean()
    std = close.rolling(20).std()
    upper = mid + 2 * std
    lower = mid - 2 * std

    last = -1
    def _f(v):
        try:
            v = float(v)
            return None if (v != v) else round(v, 4)  # NaN check
        except Exception:
            return None

    return {
        "rsi_14": _f(rsi.iloc[last]),
        "macd": {
            "macd": _f(macd_line.iloc[last]),
            "signal": _f(signal_line.iloc[last]),
            "hist": _f(hist.iloc[last]),
            "hist_prev": _f(hist.iloc[last - 1]) if len(hist) > 1 else None,
        },
        "bollinger": {
            "upper": _f(upper.iloc[last]),
            "mid": _f(mid.iloc[last]),
            "lower": _f(lower.iloc[last]),
            "close": _f(close.iloc[last]),
        },
    }


PATTERN_SYSTEM = """You are an elite technical trader analyzing a stock chart.

You are given:
1. A series of recent OHLCV candles for context.
2. Pre-computed indicators: RSI (14), MACD (12/26/9) with signal & histogram, and Bollinger Bands (20, 2).
3. OPTIONALLY a `candles_highlight` slice — these are the EXACT candles the user highlighted on the chart and wants you to focus on. If present, derive your support/resistance levels and your BUY/SELL signal PRIMARILY from this slice's behavior, using the broader candles only as context.

Combine price action + RSI + MACD + Bollinger reads into ONE coherent BUY / SELL / HOLD call and identify MULTIPLE support and resistance levels with a bullish or bearish bias and a strength rating.

Return ONLY this JSON (no markdown, no backticks):
{
  "symbol": "string",
  "interval": "string",
  "trend": "uptrend | downtrend | ranging",
  "patterns": ["string"],
  "support_levels": [
    { "price": number, "type": "bullish | bearish", "strength": "strong | weak", "label": "short tag e.g. 'demand zone'" }
  ],
  "resistance_levels": [
    { "price": number, "type": "bullish | bearish", "strength": "strong | weak", "label": "short tag e.g. 'supply zone'" }
  ],
  "support": number,
  "resistance": number,
  "indicators_read": {
    "rsi": "string — 1-line read on RSI value (overbought/oversold/neutral + meaning)",
    "macd": "string — 1-line read on MACD (cross direction, momentum)",
    "bollinger": "string — 1-line read on BB (squeeze/expansion, close vs bands)"
  },
  "momentum": "string — 1 sentence overall momentum read combining RSI + MACD",
  "signal": "BUY | SELL | HOLD",
  "entry": number,
  "stop_loss": number,
  "take_profit_1": number,
  "take_profit_2": number,
  "confidence": "low | medium | high",
  "reasoning": "string — 3 to 5 sentences integrating price action, the highlighted region (if any), RSI, MACD, and Bollinger into a clear BUY/SELL/HOLD rationale"
}

Rules:
- `support_levels` and `resistance_levels` MUST contain 1-3 entries each.
- `type: "bullish"` means the level is acting bullishly (e.g. demand support that will likely hold, OR a resistance about to break to the upside). `type: "bearish"` means the level is acting bearishly (supply rejection, weak support about to break down).
- `support` and `resistance` are single best representative scalars for legacy display.
- All price numbers must be plain JSON numbers, no $ signs, no strings.
"""


def _extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    s = text.find("{")
    e = text.rfind("}")
    if s == -1 or e == -1:
        raise ValueError(f"No JSON found: {text[:200]}")
    return json.loads(text[s : e + 1])


async def analyze_candles(
    symbol: str,
    interval: str,
    candles: List[dict],
    highlight: Optional[dict] = None,
) -> dict:
    # Trim to last 80 candles for context — keep payload small
    recent = candles[-80:]
    indicators = _compute_indicators(candles)

    highlight_slice = None
    if highlight and isinstance(highlight, dict):
        start_t = highlight.get("start_time")
        end_t = highlight.get("end_time")
        if start_t is not None and end_t is not None and end_t >= start_t:
            highlight_slice = [c for c in candles if start_t <= c["time"] <= end_t]
            # Always cap to avoid blowing the prompt
            highlight_slice = highlight_slice[-120:]

    payload = {
        "symbol": symbol,
        "interval": interval,
        "candles_recent": recent,
        "indicators": indicators,
        "last_close": recent[-1]["close"] if recent else None,
    }
    if highlight_slice:
        payload["candles_highlight"] = highlight_slice
        payload["highlight_count"] = len(highlight_slice)

    chat = LlmChat(
        api_key=EMERGENT_LLM_KEY,
        session_id=f"candles-{uuid.uuid4().hex}",
        system_message=PATTERN_SYSTEM,
    ).with_model("anthropic", "claude-sonnet-4-5-20250929")
    resp = await chat.send_message(UserMessage(text=json.dumps(payload)))
    raw = resp if isinstance(resp, str) else getattr(resp, "content", str(resp))
    result = _extract_json(raw)
    # Echo indicators so the frontend can render them too
    result.setdefault("indicators", indicators)
    return result
