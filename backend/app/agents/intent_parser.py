"""
Intent Parsing Agent (LLM-based).

Takes the raw user query from the Conversation Layer and extracts structured
intent: what is the user asking for, which region, what timeframe. This is
genuinely a language-understanding task (open-ended natural language in,
structured intent out) so an LLM is the right tool here, unlike the fetchers.
"""
from __future__ import annotations

import json
import logging
import re

from app.core.llm import LLMClient

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """[AGENT: intent_parser]
You are the Intent Parsing Agent for a marine intelligence platform used by \
Indian fishermen and coastal authorities. Given a user query, extract:

- intent: one of "pfz_query" (asking about fishing zones/conditions), \
"weather_query", "safety_check" (asking if a location is safe/in bounds), \
"general_info"
- region: the named coastal region the user means (e.g. "Kerala coast", \
"Tamil Nadu coast", "Gujarat coast", "Andhra coast", "West Bengal coast"). \
If unclear, default to the most contextually likely region.
- coordinates: if the user gives explicit coordinates (e.g. "15N 55E", \
"10.5, 76.2"), extract as {"lat": ..., "lon": ...}. Otherwise null. \
Use signed decimal degrees (N/E positive, S/W negative).
- entities: any other structured details mentioned (days ahead, boat type, etc.)
- confidence: your confidence in this parse, 0.0 to 1.0

Respond as JSON: {"intent": ..., "region": ..., "coordinates": ..., "entities": {...}, "confidence": ...}
"""


class IntentParseError(RuntimeError):
    """Raised when the intent parser LLM returns invalid JSON."""


class IntentParsingAgent:
    def __init__(self, llm_client: LLMClient):
        self._llm = llm_client

    async def run(self, query: str) -> dict:
        raw = await self._llm.complete(SYSTEM_PROMPT, query, json_mode=True)
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise IntentParseError(f"Intent parser returned invalid JSON: {raw!r}") from exc

        parsed.setdefault("coordinates", None)

        # Deterministic regex backstop for explicit coordinates -- not a
        # mock, a real parser for a pattern LLMs sometimes miss even when
        # otherwise correct. Handles "15N 55E", "10.5, 76.2", "10.5N, 76.2E".
        if not parsed.get("coordinates"):
            extracted = _extract_coordinates(query)
            if extracted:
                parsed["coordinates"] = extracted

        return parsed


def _extract_coordinates(query: str) -> dict | None:
    # Signed decimal pair: "10.5, 76.2" or "10.5 76.2"
    decimal_pair = re.search(r"(-?\d+\.?\d*)\s*[,\s]\s*(-?\d+\.?\d*)", query)
    # N/S/E/W suffixed: "15N 55E", "15 N, 55 E"
    ns_ew = re.search(
        r"(\d+\.?\d*)\s*°?\s*([NnSs])[,\s]+(\d+\.?\d*)\s*°?\s*([EeWw])", query
    )

    if ns_ew:
        lat = float(ns_ew.group(1)) * (1 if ns_ew.group(2).upper() == "N" else -1)
        lon = float(ns_ew.group(3)) * (1 if ns_ew.group(4).upper() == "E" else -1)
        return {"lat": lat, "lon": lon}

    if decimal_pair:
        lat = float(decimal_pair.group(1))
        lon = float(decimal_pair.group(2))
        # Sanity check: plausible lat/lon ranges, else likely a false match
        if -90 <= lat <= 90 and -180 <= lon <= 180:
            return {"lat": lat, "lon": lon}

    return None
