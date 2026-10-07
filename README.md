# SIH Final Project — Marine Intelligence Platform (SIH26176)

A multi-agent assistant that answers fishermen's questions ("Is it safe to fish off the Kerala coast today?")
using live weather, satellite ocean data, potential-fishing-zone (PFZ) candidates and a deterministic
maritime-boundary guardrail.

> **This version uses only OPEN data sources — no registration, no API keys, no IP-binding.**
> The only key you need is for the LLM (unchanged from the original).

## What changed vs. the original `orca-init`

| Layer | Before (needed sign-up / proof) | Now (open, key-less) |
|---|---|---|
| Weather, wind, temperature | IMD API portal (`api.imd.gov.in`) — account + IP-bound API key + JWT | **Open-Meteo Forecast API** (CC BY 4.0) |
| Wave height / sea state | Estimated from IMD's text "sea condition" descriptor | **Open-Meteo Marine API** — modelled significant wave height, swell, period, SST |
| Marine warnings | IMD coastal / sea / port bulletins | Derived hazards from wind, gusts, waves, WMO weather code, visibility **plus a 24-h look-ahead** (peak gusts, waves, thunderstorms) |
| Satellite SST + chlorophyll | INCOIS ERDDAP (+ MOSDAC granule search / account for downloads) | **NOAA CoastWatch ERDDAP** — NASA JPL MUR SST, VIIRS / MODIS chlorophyll-a |
| PFZ | Derived from the above | Derived from the new open SST/chlorophyll (same engine) |
| EEZ + coastline geometry | Marine Regions / Natural Earth (open) | unchanged |
| **LLM + all 6 agents, pipeline, schemas, API, frontend** | — | **unchanged** (only two provenance labels in the UI were renamed) |

Removed: `imd_client.py`, `mosdac_client.py`, `fetch_mosdac_granule.py`, `test_imd_parsers.py`.
Added: `open_meteo_client.py`, `erddap_client.py`, `http_util.py`, `tests/test_open_data.py`.
The data contracts in `app/models/schemas.py` are untouched, so every downstream agent works as before.

## Architecture / pipeline (unchanged)

```
User query
  → Intent Parsing Agent (LLM)
  → Orchestrator / Planner Agent (LLM)
  → ┌ Weather Fetcher  ── Open-Meteo forecast + marine
    ├ Ocean Fetcher    ── NOAA CoastWatch ERDDAP (SST, chlorophyll)
    ├ PFZ Fetcher      ── derived from the ocean data (SST fronts + chlorophyll)
    ├ Geospatial       ── region bounds / centroid
    └ Maritime Boundary Guardrail (deterministic, Shapely) ── HARD STOP → Safety Alert
  → Correlation Agent (LLM)
  → Risk Assessment Agent (rules for the level + LLM for the explanation)
  → Evaluation Agent (LLM) ⟲ regeneration loop (max 2)
  → Response Synthesis Agent (LLM)
  → answer + full agent trace + map layers
```

Fetchers are plain deterministic code (no LLM). The risk *level* is rule-based and auditable
(`app/agents/risk_assessment.py`).

## Repository layout

```
backend/    FastAPI app (agents, fetchers, guardrail, pipeline), scripts, tests
frontend/   React + Vite chat UI with map
README.md   this file
```

## Quick start

### Backend
```bash
cd backend
python3 -m venv venv && source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                   # put your OPENAI_API_KEY in .env
uvicorn app.main:app --reload                          # http://localhost:8000
```
Optional one-time geometry downloads (both open; without them the app uses a labelled placeholder):
```bash
python scripts/fetch_real_eez.py         # India EEZ polygon (Marine Regions, MRGID 8480)
python scripts/fetch_real_coastline.py   # India outline for the map (Natural Earth / geoBoundaries)
```
Try it:
```bash
curl -X POST http://localhost:8000/api/query -H "Content-Type: application/json" \
     -d '{"query": "Is it safe to fish off the Kerala coast today?"}'
```
`GET /health` shows which sources are wired; `GET /api/geo` serves the map geometry.

### Frontend
```bash
cd frontend
cp .env.local.example .env.local     # points the UI at the backend
npm install && npm run dev
```

## Configuration (`backend/.env`)

| Variable | Required | Default |
|---|---|---|
| `OPENAI_API_KEY` | **yes** (LLM agents) | — |
| `OPENAI_MODEL` / `OPENAI_BASE_URL` | no | `gpt-4o` / OpenAI URL (any OpenAI-compatible endpoint works) |
| `OPEN_METEO_BASE`, `OPEN_METEO_MARINE_BASE` | no | public Open-Meteo hosts |
| `ERDDAP_BASE` | no | `https://coastwatch.pfeg.noaa.gov/erddap` |

## How the open data layer works

**Weather (`app/fetchers/weather_fetcher.py`)** — one *offshore reference point* per region
(e.g. off Kochi for "kerala coast"; see `REGION_POINTS`) is queried on the Forecast and Marine APIs
concurrently. It yields wind (km/h), gusts, air temperature, wave height, WMO weather code, visibility,
and a 24-hour hourly outlook. Hazards (`high_wind`, `rough_sea`, `thunderstorm`, `low_visibility`,
`forecast_gusts`, `forecast_rough_sea`, `forecast_thunderstorm`) are produced with thresholds documented
at the top of the file. Wave height is now a **model output** rather than an estimate from a text descriptor.

**Ocean (`app/fetchers/ocean_fetcher.py`, `erddap_client.py`)** — dataset candidates are listed in
`app/core/erddap_datasets.json` (SST: `jplMURSST41` → `noaacrwsstDaily`; chlorophyll: VIIRS daily →
MODIS 8-day → MODIS monthly). For each candidate the client *introspects* ERDDAP's `/info` page (axis
order, altitude axis, ascending/descending latitude, grid spacing, variable name), builds a thinned
(~0.25°) griddap query for the latest time slice, and moves to the next candidate if a dataset is retired
or cloud-masked (common for chlorophyll in the monsoon). Kelvin SST is auto-converted. If no candidate
works, the request raises — no fake data.

**PFZ (`pfz_derived_engine.py`)** — unchanged engine: SST-gradient fronts + chlorophyll thresholds.

### Be precise about what is real (for judges)
* Weather and wave values are **numerical-model output** (Open-Meteo), not buoy/station observations.
* PFZ zones are **derived** from open satellite SST/chlorophyll with the published principle
  (SST fronts + chlorophyll). They are **not** the official INCOIS PFZ advisory (that has no public API);
  every PFZ bundle carries this disclaimer and is tagged `derived`.
* Every response's `map_layers.weather_notes` / `ocean_notes` cite the exact endpoints and datasets used.

### Fail-loud philosophy (kept from the original)
No mock mode. A missing `OPENAI_API_KEY`, an unreachable API, a null wave height, or no usable ocean
dataset raises a clear error rather than degrading silently. The only tolerance added is **one retry**
on transient network errors / HTTP 429/5xx (`app/fetchers/http_util.py`).
Do a dry run on the network you will demo on.

### Small-token LLM providers (e.g. Groq free tier, 8000 TPM)
The satellite grids are ~200 pixels each, so `app/core/llm_payload.py` compacts the ocean/PFZ data
(summary statistics + 8 representative pixels, no map polygons) before it goes into the Correlation
prompt (~9k → ~1.5k tokens). The map and PFZ engine still use the full-resolution data. To use Groq set
`OPENAI_BASE_URL=https://api.groq.com/openai/v1`, `OPENAI_API_KEY=<groq key>`, `OPENAI_MODEL=openai/gpt-oss-120b`.

## Verification

```bash
cd backend
python -m unittest discover -s tests -t .     # offline: parsers, hazard rules, ERDDAP query builder, PFZ engine
python scripts/smoke_test.py                  # LIVE check of Open-Meteo + ERDDAP + geometry (needs internet)
python scripts/discover_erddap_datasets.py    # optional: verify/refresh ERDDAP dataset candidates
```
The smoke test prints PASS/WARN/FAIL per source and checks SST plausibility (20–35 °C for Indian waters).

## Usage terms
Open-Meteo is free for non-commercial use (<10 000 calls/day) under CC BY 4.0 — attribution is included in
the provenance notes. NOAA CoastWatch / NASA data are public domain. Marine Regions (EEZ) is CC BY.

## Known limitations / next steps
* The marine wave model is null over land/very near shore, hence offshore reference points; edit
  `REGION_POINTS` to add regions (and `REGION_BOUNDS` in `ocean_fetcher.py`).
* Chlorophyll can be sparse during the monsoon; the fetcher falls back to 8-day/monthly composites and says so in the notes.
* Mobile app / streaming trace not built (as before).
