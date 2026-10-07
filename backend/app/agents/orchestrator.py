"""
Planner / Orchestrator Agent (LLM-based).

Decides which fetchers and downstream agents are needed for a given parsed
intent, and always triggers the maritime boundary guardrail in parallel
(per the architecture diagram, this runs alongside the fetchers, not after
them, so a hard-stop can happen as early as possible).

No fallback plan: if the LLM returns an empty or malformed plan, this
raises rather than silently substituting a hardcoded default -- consistent
with the rest of the pipeline's fail-loud policy.
"""
from __future__ import annotations

import json
import logging

from app.core.llm import LLMClient

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """[AGENT: orchestrator]
You are the Planner/Orchestrator Agent. Given a parsed intent, decide which \
of the following steps are needed: "weather_fetch", "ocean_fetch", \
"geospatial_fetch", "pfz_fetch". The boundary check always runs regardless \
of your plan, so do not include it.

Respond as JSON: {"plan": [...], "reasoning": "..."}
"""


class OrchestratorPlanError(RuntimeError):
    """Raised when the orchestrator LLM returns an empty or malformed plan."""


class OrchestratorAgent:
    def __init__(self, llm_client: LLMClient):
        self._llm = llm_client

    async def run(self, parsed_intent: dict) -> dict:
        user_prompt = json.dumps(parsed_intent)
        raw = await self._llm.complete(SYSTEM_PROMPT, user_prompt, json_mode=True)

        try:
            result = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise OrchestratorPlanError(f"Orchestrator returned invalid JSON: {raw!r}") from exc

        plan = result.get("plan") or []
        if not plan:
            raise OrchestratorPlanError(f"Orchestrator returned an empty plan: {result!r}")

        return result
