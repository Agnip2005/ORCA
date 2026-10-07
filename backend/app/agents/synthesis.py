"""
Response Synthesis Agent (LLM-based).

Final step of the happy path: turns the verified correlation summary and
risk assessment into the actual chat response shown to the user. This is
bypassed entirely if the Maritime Boundary Guardrail hard-stops the
pipeline (per the architecture diagram, the guardrail's "hard stop" edge
goes straight to the Safety Alert, skipping synthesis).
"""
from __future__ import annotations

from app.models.schemas import RiskAssessment

SYSTEM_PROMPT = """[AGENT: synthesis]
You are the Response Synthesis Agent for a marine intelligence platform \
used by Indian fishermen. Given a correlation summary and a risk \
assessment, write a concise, friendly, plain-language chat response (3-5 \
sentences) that a fisherman with a phone would actually want to read. \
Mention the risk level naturally. Do not use jargon.
"""


class SynthesisAgent:
    def __init__(self, llm_client):
        self._llm = llm_client

    async def run(self, correlation_summary: str, risk_assessment: RiskAssessment, query: str) -> str:
        user_prompt = (
            f"User query: {query}\n"
            f"Correlation summary: {correlation_summary}\n"
            f"Risk level: {risk_assessment.risk_level.value}\n"
            f"Risk explanation: {risk_assessment.explanation}"
        )
        return await self._llm.complete(SYSTEM_PROMPT, user_prompt, json_mode=False)
