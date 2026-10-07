"""
Evaluation / Verification Agent (LLM-based).

Checks the risk assessment's explanation against the correlation summary
and the underlying data for consistency. If it finds a mismatch (e.g. the
explanation contradicts the correlation summary, or claims something the
data doesn't support), it signals the orchestration loop to regenerate --
per the architecture diagram's dotted "mismatch found - regenerate" edges
back to both the Correlation Agent and the Risk Assessment Agent.
"""
from __future__ import annotations

import json
import logging

from app.models.schemas import RiskAssessment

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """[AGENT: evaluation]
You are the Evaluation/Verification Agent. You are given a correlation \
summary and a risk assessment explanation for the same region. Check \
whether they are consistent with each other -- flag a mismatch if the risk \
explanation contradicts the correlation summary, ignores a hazard the \
correlation summary raised, or makes an unsupported claim.

Respond as JSON: {"verified": true/false, "mismatch_found": true/false, "notes": "..."}
"""


class EvaluationParseError(RuntimeError):
    """Raised when the evaluation agent LLM returns invalid JSON."""


class EvaluationAgent:
    def __init__(self, llm_client):
        self._llm = llm_client

    async def run(self, correlation_summary: str, risk_assessment: RiskAssessment) -> dict:
        payload = {
            "correlation_summary": correlation_summary,
            "risk_level": risk_assessment.risk_level.value,
            "risk_explanation": risk_assessment.explanation,
        }
        raw = await self._llm.complete(SYSTEM_PROMPT, json.dumps(payload), json_mode=True)

        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise EvaluationParseError(f"Evaluation agent returned invalid JSON: {raw!r}") from exc
