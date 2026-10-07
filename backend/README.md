# Backend — Marine Intelligence Platform

FastAPI service. **See the top-level [README.md](../README.md)** for architecture, setup, configuration and
the open-data design (Open-Meteo + NOAA CoastWatch ERDDAP — no registration or keys except the LLM key).

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # set OPENAI_API_KEY
uvicorn app.main:app --reload
python -m unittest discover -s tests -t .
python scripts/smoke_test.py   # live check, needs internet
```

```
app/agents/     intent_parser, orchestrator, correlation, risk_assessment, evaluation, synthesis (LLM, unchanged)
app/fetchers/   weather_fetcher + open_meteo_client, ocean_fetcher + erddap_client, pfz_*, geospatial, http_util
app/core/       config, llm, boundary_guardrail, pipeline, erddap_datasets.json, EEZ geojson
scripts/        smoke_test, discover_erddap_datasets, fetch_real_eez, fetch_real_coastline
tests/          test_open_data, test_pfz_engine
```
