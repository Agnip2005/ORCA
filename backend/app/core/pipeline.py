"""
Pipeline orchestration.

Wires together every agent per the architecture diagram's control flow:

1. Intent Parsing Agent parses the raw query.
2. Orchestrator Agent plans which fetchers to run.
3. Fetchers run CONCURRENTLY, alongside the Maritime Boundary Guardrail
   (deterministic, not LLM) which can HARD-STOP the pipeline -- if it
   fires, we go straight to a Safety Alert and skip synthesis entirely,
   exactly like the diagram's "hard stop - bypasses synthesis" edge.
4. Correlation Agent reasons over the fetched data.
5. Risk Assessment Agent computes risk (rules) + explains it (LLM).
6. Evaluation Agent checks correlation vs risk explanation for mismatches.
   If a mismatch is found, we regenerate: back to Correlation, then Risk
   Assessment again, capped at MAX_REGENERATIONS to avoid infinite loops.
7. Synthesis Agent produces the final chat response.

Every step is logged into an AgentTrace list so the demo UI (and judges)
can see the pipeline execute, not just the end result.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from app.agents.correlation import CorrelationAgent
from app.agents.evaluation import EvaluationAgent
from app.agents.intent_parser import IntentParsingAgent
from app.agents.orchestrator import OrchestratorAgent
from app.agents.risk_assessment import RiskAssessmentAgent
from app.agents.synthesis import SynthesisAgent
from app.core.boundary_guardrail import maritime_boundary_guardrail
from app.core.llm import llm_client
from app.core.llm_payload import compact_ocean, compact_pfz
from app.fetchers.geospatial_fetcher import GeospatialFetcher
from app.fetchers.ocean_fetcher import OceanFetcher
from app.fetchers.pfz_fetcher import PFZFetcher
from app.fetchers.weather_fetcher import WeatherFetcher
from app.models.schemas import AgentTrace, GeoPoint, PipelineResponse

logger = logging.getLogger(__name__)

MAX_REGENERATIONS = 2


class Pipeline:
    def __init__(self):
        self.intent_parser = IntentParsingAgent(llm_client)
        self.orchestrator = OrchestratorAgent(llm_client)
        self.correlation_agent = CorrelationAgent(llm_client)
        self.risk_agent = RiskAssessmentAgent(llm_client)
        self.evaluation_agent = EvaluationAgent(llm_client)
        self.synthesis_agent = SynthesisAgent(llm_client)

        self.ocean_fetcher = OceanFetcher()
        self.weather_fetcher = WeatherFetcher()
        self.pfz_fetcher = PFZFetcher()
        self.geospatial_fetcher = GeospatialFetcher()

    async def run(self, query: str) -> PipelineResponse:
        trace: list[AgentTrace] = []

        # --- 1. Intent parsing ---
        t0 = datetime.now(timezone.utc)
        parsed_intent = await self.intent_parser.run(query)
        trace.append(_trace("Intent Parsing Agent", t0, f"intent={parsed_intent.get('intent')}, region={parsed_intent.get('region')}"))

        region = parsed_intent.get("region", "Kerala coast")
        explicit_coords = parsed_intent.get("coordinates")

        # --- 2. Orchestration / planning ---
        t0 = datetime.now(timezone.utc)
        plan_result = await self.orchestrator.run(parsed_intent)
        plan = plan_result.get("plan", [])
        trace.append(_trace("Planner / Orchestrator Agent", t0, f"plan={plan}"))

        # --- 3. Fetchers run concurrently, guardrail runs alongside them ---
        geo_result = await self.geospatial_fetcher.fetch(region)
        centroid = geo_result.get("centroid")

        # Prefer explicit coordinates the user actually gave over the
        # region centroid -- the guardrail should check where the user is
        # asking about, not just the general area.
        if explicit_coords:
            check_point = GeoPoint(lat=explicit_coords["lat"], lon=explicit_coords["lon"])
        elif centroid:
            check_point = GeoPoint(**centroid)
        else:
            check_point = GeoPoint(lat=10.0, lon=76.0)

        t0 = datetime.now(timezone.utc)
        # weather/ocean fetch concurrently (independent of each other);
        # PFZ now depends on ocean data (it derives fronts from real
        # SST/chlorophyll), so it runs AFTER ocean rather than alongside it.
        # If pfz_fetch is requested without ocean_fetch in the plan, we
        # still fetch ocean data for it (PFZ needs it) but don't expose
        # ocean separately unless the plan also asked for it.
        fetch_tasks = {}
        if "weather_fetch" in plan:
            fetch_tasks["weather"] = self.weather_fetcher.fetch(region)
        if "ocean_fetch" in plan or "pfz_fetch" in plan:
            fetch_tasks["ocean"] = self.ocean_fetcher.fetch(region)

        boundary_task = asyncio.create_task(_run_boundary_check(check_point))
        fetch_results = {}
        if fetch_tasks:
            completed = await asyncio.gather(*fetch_tasks.values())
            fetch_results = dict(zip(fetch_tasks.keys(), completed))

        if "pfz_fetch" in plan:
            ocean_for_pfz = fetch_results.get("ocean")
            if ocean_for_pfz is None:
                # Should be unreachable: line 102 guarantees ocean_fetch runs
                # whenever pfz_fetch is planned. Explicit check so a future
                # change to that guarantee fails clearly here, not as a
                # confusing TypeError inside the derived engine.
                raise RuntimeError(
                    "pfz_fetch was planned but ocean data was not fetched -- "
                    "PFZ derivation requires ocean data as an input."
                )
            fetch_results["pfz"] = await self.pfz_fetcher.fetch(region, ocean_data=ocean_for_pfz)
        boundary_check = await boundary_task

        trace.append(_trace(
            "Fetchers (concurrent)", t0,
            f"ran={list(fetch_results.keys())}",  # 'ocean' may appear here even if only pfz_fetch was planned -- pfz depends on it
        ))
        trace.append(_trace(
            "Maritime Boundary Guardrail (deterministic)", t0,
            boundary_check.message,
        ))

        # --- HARD STOP: guardrail fired, bypass synthesis entirely ---
        if not boundary_check.is_safe:
            trace.append(_trace("Safety Alert", datetime.now(timezone.utc), "Pipeline halted: boundary violation"))
            return PipelineResponse(
                query=query,
                chat_response=boundary_check.message,
                risk_assessment=None,
                boundary_check=boundary_check,
                safety_alert=True,
                trace=trace,
                map_layers={"region": region, "centroid": centroid},
            )

        weather = fetch_results.get("weather")
        ocean = fetch_results.get("ocean")
        pfz = fetch_results.get("pfz")

        # --- 4-6. Correlation -> Risk -> Evaluation, with regeneration loop ---
        regeneration_count = 0
        correlation_result = None
        risk_assessment = None
        evaluation_result = {"verified": True, "mismatch_found": False}

        # Prompt copies are compacted (stats + small sample) so the LLM request
        # stays within small token-per-minute limits; full data feeds the map.
        ocean_llm, pfz_llm = compact_ocean(ocean), compact_pfz(pfz)

        while True:
            t0 = datetime.now(timezone.utc)
            correlation_result = await self.correlation_agent.run(ocean_llm, weather, pfz_llm)
            trace.append(_trace("Correlation & Reasoning Agent", t0, correlation_result.get("correlation_summary", "")[:120]))

            t0 = datetime.now(timezone.utc)
            risk_assessment = await self.risk_agent.run(weather)
            trace.append(_trace("Risk Assessment Agent", t0, f"risk={risk_assessment.risk_level.value}"))

            t0 = datetime.now(timezone.utc)
            evaluation_result = await self.evaluation_agent.run(
                correlation_result.get("correlation_summary", ""), risk_assessment
            )
            trace.append(_trace("Evaluation / Verification Agent", t0, f"mismatch={evaluation_result.get('mismatch_found')}"))

            if not evaluation_result.get("mismatch_found") or regeneration_count >= MAX_REGENERATIONS:
                break

            regeneration_count += 1
            logger.info("Mismatch found, regenerating (attempt %d/%d)", regeneration_count, MAX_REGENERATIONS)

        # --- 7. Synthesis ---
        t0 = datetime.now(timezone.utc)
        chat_response = await self.synthesis_agent.run(
            correlation_result.get("correlation_summary", ""), risk_assessment, query
        )
        trace.append(_trace("Response Synthesis Agent", t0, "chat response generated"))

        map_layers = {
            "region": region,
            "centroid": centroid,
            "pfz_advisories": [a.model_dump(mode="json") for a in pfz.advisories] if pfz else [],
            "sst_readings": [r.model_dump(mode="json") for r in ocean.sst_readings] if ocean else [],
            # Provenance strings so API consumers can see exactly which Indian
            # open data sources (Open-Meteo endpoints, NOAA CoastWatch ERDDAP datasets) produced these
            # values, and which parts are documented estimates.
            "weather_notes": weather.notes if weather else None,
            "ocean_notes": ocean.notes if ocean else None,
        }

        return PipelineResponse(
            query=query,
            chat_response=chat_response,
            risk_assessment=risk_assessment,
            boundary_check=boundary_check,
            safety_alert=False,
            trace=trace,
            regeneration_count=regeneration_count,
            map_layers=map_layers,
        )

    async def aclose(self):
        await self.ocean_fetcher.aclose()
        await self.weather_fetcher.aclose()


async def _run_boundary_check(point: GeoPoint):
    # Guardrail is sync/deterministic; wrapped in a coroutine so it can run
    # concurrently with the async fetchers via asyncio.gather-style timing.
    return maritime_boundary_guardrail.check(point)


def _trace(agent_name: str, started_at: datetime, summary: str) -> AgentTrace:
    return AgentTrace(
        agent_name=agent_name,
        started_at=started_at,
        finished_at=datetime.now(timezone.utc),
        summary=summary,
    )


pipeline = Pipeline()
