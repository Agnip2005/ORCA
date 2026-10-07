"""
Risk Assessment Agent (Rules + LLM explanation).

Per the architecture diagram, this agent is explicitly hybrid: the risk
LEVEL itself is computed by deterministic rules (so it's reproducible and
defensible -- "why is this moderate risk" should never depend on LLM
sampling variance), while the LLM is used only to turn that rules-based
result into a natural-language explanation a fisherman can actually read.

This mirrors the same code-vs-LLM split used for fetchers vs reasoning
agents, applied within a single agent.
"""
from __future__ import annotations

import json

from app.core.llm import LLMClient
from app.models.schemas import RiskAssessment, RiskLevel, WeatherDataBundle

SYSTEM_PROMPT = """[AGENT: risk_assessment]
You are the Risk Assessment Agent. You are given a pre-computed risk level \
and the factors that produced it. Write a short, plain-language explanation \
(2-3 sentences) suitable for a fisherman with no technical background. Do \
not change the risk level or invent new factors.

Respond as JSON: {"explanation": "..."}
"""


class RiskExplanationError(RuntimeError):
    """Raised when the risk assessment LLM returns no usable explanation."""


class RiskAssessmentAgent:
    def __init__(self, llm_client: LLMClient):
        self._llm = llm_client

    def _compute_risk_level(self, weather: WeatherDataBundle | None) -> tuple[RiskLevel, list[str]]:
        """Deterministic rules -- no LLM. This is the part judges can audit line by line."""
        if weather is None:
            return RiskLevel.MODERATE, ["insufficient weather data"]

        factors = []
        score = 0

        if weather.wave_height_m >= 3.0:
            score += 3
            factors.append(f"high wave height ({weather.wave_height_m}m)")
        elif weather.wave_height_m >= 1.8:
            score += 2
            factors.append(f"moderate wave height ({weather.wave_height_m}m)")
        else:
            factors.append(f"manageable wave height ({weather.wave_height_m}m)")

        if weather.wind_speed_kmh >= 50:
            score += 3
            factors.append(f"high wind speed ({weather.wind_speed_kmh} km/h)")
        elif weather.wind_speed_kmh >= 30:
            score += 1
            factors.append(f"elevated wind speed ({weather.wind_speed_kmh} km/h)")

        for hazard in weather.hazards:
            severity_score = {"low": 0, "moderate": 2, "high": 3, "severe": 5}.get(hazard.severity, 1)
            score += severity_score
            factors.append(f"{hazard.hazard_type} ({hazard.severity})")

        if score >= 6:
            level = RiskLevel.CRITICAL
        elif score >= 4:
            level = RiskLevel.HIGH
        elif score >= 2:
            level = RiskLevel.MODERATE
        else:
            level = RiskLevel.LOW

        return level, factors

    async def run(self, weather: WeatherDataBundle | None) -> RiskAssessment:
        risk_level, factors = self._compute_risk_level(weather)

        user_prompt = f"Risk level: {risk_level.value}\nFactors: {', '.join(factors)}"
        raw = await self._llm.complete(SYSTEM_PROMPT, user_prompt, json_mode=True)

        parsed = json.loads(raw)  # raises json.JSONDecodeError -> propagates, no silent fallback
        explanation = parsed.get("explanation")
        if not explanation:
            raise RiskExplanationError(f"Risk assessment LLM returned no explanation: {parsed!r}")

        return RiskAssessment(risk_level=risk_level, factors=factors, explanation=explanation)
