"""
Correlation & Reasoning Agent (LLM-based).

Takes the raw outputs of the fetchers (ocean, weather, geospatial, PFZ) and
finds the actual relationships between them -- e.g. does the SST/chlorophyll
pattern line up with what the PFZ advisory says, are the weather conditions
consistent with safe operation in the advised zone. This is a genuine
reasoning task over heterogeneous structured data, which is why it's an LLM
agent rather than fetcher code.
"""
from __future__ import annotations

import json
import logging

from app.core.llm import LLMClient
from app.models.schemas import OceanDataBundle, PFZDataBundle, WeatherDataBundle

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """[AGENT: correlation]
You are the Correlation & Reasoning Agent for a marine intelligence \
platform. You are given ocean data (SST, chlorophyll), weather data, and \
PFZ advisory data for a region. Identify how these datasets relate: do \
ocean conditions support the PFZ advisory, are there any inconsistencies, \
what does the weather mean for accessing the advised zones.

Respond as JSON: {"correlation_summary": "...", "confidence": 0.0-1.0}
"""


class CorrelationParseError(RuntimeError):
    """Raised when the correlation agent LLM returns invalid JSON."""


class CorrelationAgent:
    def __init__(self, llm_client: LLMClient):
        self._llm = llm_client

    async def run(
        self,
        ocean: OceanDataBundle | None,
        weather: WeatherDataBundle | None,
        pfz: PFZDataBundle | None,
    ) -> dict:
        payload = {
            "ocean": ocean.model_dump(mode="json") if ocean else None,
            "weather": weather.model_dump(mode="json") if weather else None,
            "pfz": pfz.model_dump(mode="json") if pfz else None,
        }
        raw = await self._llm.complete(SYSTEM_PROMPT, json.dumps(payload, default=str), json_mode=True)

        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise CorrelationParseError(f"Correlation agent returned invalid JSON: {raw!r}") from exc
