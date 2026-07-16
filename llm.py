"""Claude Sonnet 4.5 wrapper for V5 / V6 / Going-concern / V2 distortion / final reasoning."""
import json
import os
import re
import uuid
from typing import Optional

from emergentintegrations.llm.chat import LlmChat, UserMessage

EMERGENT_LLM_KEY = os.environ.get("EMERGENT_LLM_KEY", "")

SYSTEM_PROMPT = """You are an expert quantitative trading analyst. You will receive computed quantitative
variables (V1 range, V2 book value, V3 composite, V4 institutional/insider) for a stock, plus an excerpt of
the most recent SEC filing. Your job is to add the QUALITATIVE LAYER:

1. V2 distortion analysis — does V2 reflect genuine inflow/outflow, or is it distorted by Buybacks / SBC /
   Debt/leverage / IPO / ATM raise / Acquisition goodwill / Genuine losses? Return ONE primary distortion_type
   (or null) and a 1-sentence distortion_note.
2. V5 AI Displacement Index rating — pick exactly one of:
     "AI Infrastructure" (Strongly Positive),
     "AI Enabler"        (Positive),
     "AI Neutral"        (Neutral),
     "AI Competitive"    (Negative),
     "AI Displaced"      (Strongly Negative).
   Provide a 2-4 sentence reasoning.
3. V6 Pre-Earnings decision IF earnings are within 14 days. BUY / SELL / SKIP / N/A. Apply the rules strictly.
4. Going-concern — scan the filing excerpt for "substantial doubt about its ability to continue as a going
   concern" or equivalent. Return boolean + note.
5. v3_invalid_reason — if V2 is negative AND it's driven by debt/leverage/buybacks (not genuine operational
   losses), set invalid=true and explain. Otherwise invalid=false.
6. final_signal — synthesize everything (V4 supreme override first, then going concern, then V3 + V5
   amplification). Choose ONE of: "Strong Long", "Long", "Watch", "Skip", "Bear Watch", "Bear",
   "Do Not Enter", "Invalid".
7. final_reasoning — 3 to 5 sentences combining all signals into a clear actionable verdict.

Return ONLY a strict JSON object with this exact schema. No markdown, no backticks, no commentary.

{
  "v2_distortion_type": "Buybacks | SBC | Debt/leverage | IPO | ATM raise | Acquisition goodwill | Genuine losses | null",
  "v2_distortion_note": "string or null",
  "v3_invalid": boolean,
  "v3_invalid_reason": "string or null",
  "v5_rating": "AI Infrastructure | AI Enabler | AI Neutral | AI Competitive | AI Displaced",
  "v5_signal": "Strongly Positive | Positive | Neutral | Negative | Strongly Negative",
  "v5_reasoning": "string",
  "v6_decision": "BUY | SELL | SKIP | N/A",
  "v6_criteria_met": "string",
  "v6_reasoning": "string",
  "going_concern": boolean,
  "going_concern_note": "string or null",
  "final_signal": "Strong Long | Long | Watch | Skip | Bear Watch | Bear | Do Not Enter | Invalid",
  "final_reasoning": "string"
}"""


def _extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"No JSON object found in LLM response: {text[:200]}")
    return json.loads(text[start : end + 1])


async def analyze_meta(payload: dict, filing_excerpt: str = "") -> dict:
    user_text = (
        f"INPUT (computed quantitative facts):\n{json.dumps(payload, indent=2)}\n\n"
        f"SEC FILING EXCERPT (truncated, scan for going-concern language):\n{filing_excerpt[:15000]}\n\n"
        "Return strict JSON per schema."
    )
    chat = LlmChat(
        api_key=EMERGENT_LLM_KEY,
        session_id=f"quant-{uuid.uuid4().hex}",
        system_message=SYSTEM_PROMPT,
    ).with_model("anthropic", "claude-sonnet-4-5-20250929")

    resp = await chat.send_message(UserMessage(text=user_text))
    raw = resp if isinstance(resp, str) else getattr(resp, "content", str(resp))
    return _extract_json(raw)
