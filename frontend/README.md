# Marine Intelligence Platform — Desktop Command-Center UI

Split-panel desktop UI: chat + live agent-pipeline trace on the left,
animated map on the right. Talks to the FastAPI backend over real HTTP —
this is a standalone Vite/React app, not a Claude.ai artifact (artifacts
can't reach `localhost`, which is why this exists as its own project).

## Run it

You need the backend running first (see the backend's own README).

```bash
# Terminal 1 — backend
cd marine-intel-backend
source venv/bin/activate
uvicorn app.main:app --reload
# runs on http://localhost:8000

# Terminal 2 — frontend
cd marine-intel-frontend
npm install
npm run dev
# runs on http://localhost:5173
```

Open `http://localhost:5173`. If the backend isn't running, the header dot
turns red and the chat panel shows a clear connection error instead of
failing silently.

If your backend runs somewhere other than `localhost:8000`:
```bash
cp .env.local.example .env.local
# edit VITE_API_BASE in .env.local
```

## What's real vs illustrative here

- **The chat/query flow is real** — every message hits `POST /api/query`
  on your actual backend and renders the actual `PipelineResponse`
  (chat text, risk level, PFZ advisories, boundary check, agent trace).
- **The agent trace is real data, replayed for visibility** — the backend
  runs the whole pipeline synchronously and returns the complete `trace[]`
  array in one response (see `PipelineResponse.trace` in the backend's
  `app/models/schemas.py`). This UI reveals those real steps one at a time
  client-side so the sequence is visible, rather than dumping the full list
  at once. It is **not** a live stream from the server — if you want true
  step-by-step streaming (each agent's result pushed the moment it
  finishes), the backend would need a WebSocket or Server-Sent-Events
  endpoint instead of a single blocking POST. Worth being precise about
  this distinction if judges ask "is that live."
- **The map renders real geometry served by the backend** — `GET /api/geo`
  returns the real India coastline (one-time
  `backend/scripts/fetch_real_coastline.py`), the *exact* EEZ polygon the
  backend's boundary guardrail checks against (real Marine Regions trace or
  labeled placeholder), and the five coastal region display boxes. Until the
  coastline script has been run, the UI falls back to the bundled
  illustrative silhouette — and the provenance box in the map's corner
  always says which one you're looking at, plus whether the EEZ is
  `[DEMO DATA]`. Fishing zones render as **non-uniform polygons** (a ±5 km
  corridor around each detected SST front, computed by the backend), not
  uniform boxes or bare pins.
- **PFZ pins, risk color, boundary crosshair** all render directly from
  whatever the backend returns — including its own honesty labels
  (`source: live` vs `source: derived`, see the backend's `DataSourceStatus`
  in `app/models/schemas.py`) in `source` fields.
- **Per-answer provenance badges** — every assistant answer shows small
  expandable chips ("Open-Meteo weather sources", "satellite ocean sources"). Clicking
  one reveals the exact provenance string the backend returned: which Open-Meteo
  endpoint and offshore reference point produced wind/temperature/waves, and
  which NOAA CoastWatch ERDDAP datasets supplied SST and chlorophyll. Nothing is paraphrased — it's the API's own
  `map_layers.weather_notes` / `map_layers.ocean_notes` text, rendered
  verbatim.
- **The backend has no mock mode.** If the backend's OpenAI, Open-Meteo
  or ERDDAP calls fail, `POST /api/query` will return an error response
  instead of degraded data — the UI's `connectionError` state handles a
  backend that's down, but not yet a mid-query failure returned as an
  HTTP error status. Worth testing what that looks like in the UI before
  a live demo, so an API failure doesn't look like a silent freeze.

## Project structure

```
src/
  App.jsx       everything: MapPanel, AgentTracePanel, ChatMessage, main App
  index.css     minimal reset
index.html      Google Fonts (IBM Plex Mono + Sans) loaded here
```

Kept as a single `App.jsx` deliberately for a hackathon-scale project —
split into components if it grows past this.

## Suggested next additions

- Surface `data_source` per fetcher (live / derived) as small badges in
  the UI — directly answers "is this real" without judges needing to ask.
- A toggle to inspect the full `PipelineResponse` JSON (raw agent
  reasoning, correlation summary, risk factors) for technical judges who
  want to see under the hood.
- WebSocket/SSE backend endpoint if true live streaming of agent progress
  becomes important — current version is an honest approximation, not that.
