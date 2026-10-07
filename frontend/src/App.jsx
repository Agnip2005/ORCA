import { useState, useRef, useEffect, useCallback } from "react";

// ============================================================================
// DESIGN TOKENS
// ============================================================================
const T = {
  bg: "#0A1420",
  panel: "#0F1F30",
  panelRaised: "#132437",
  elevated: "#1B3A52",
  border: "#1F3A50",
  borderBright: "#2C5570",
  text: "#F0F4F5",
  textDim: "#7E96A8",
  textFaint: "#4E6478",
  cyan: "#5EC8D8",
  amber: "#E8A33D",
  red: "#E8654D",
  green: "#5DBE8A",
};

const RISK_COLOR = { low: T.green, moderate: T.amber, high: T.red, critical: T.red };

// Backend base URL. Override at build time with VITE_API_BASE if the
// backend isn't on localhost:8000 (see .env.local.example).
const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000";

const REGIONS = {
  "Kerala coast": { lat: 10.25, lon: 76.0 },
  "Tamil Nadu coast": { lat: 10.75, lon: 79.25 },
  "Gujarat coast": { lat: 21.75, lon: 70.25 },
  "Andhra coast": { lat: 16.25, lon: 82.25 },
  "West Bengal coast": { lat: 21.85, lon: 88.25 },
};

// ============================================================================
// MAP — SVG equirectangular projection, animated PFZ pins, boundary marker
// ============================================================================
const MAP_W = 640;
const MAP_H = 620;
const LON_MIN = 65, LON_MAX = 95, LAT_MIN = 4, LAT_MAX = 25;

function project(lat, lon) {
  const x = ((lon - LON_MIN) / (LON_MAX - LON_MIN)) * MAP_W;
  const y = ((LAT_MAX - lat) / (LAT_MAX - LAT_MIN)) * MAP_H;
  return [x, y];
}

// GeoJSON -> SVG path helpers. Rings are [lon, lat] pairs; polygons are
// arrays of rings (outer first), multipolygons arrays of polygons.
function ringToPath(ring) {
  if (!ring || ring.length === 0) return "";
  return (
    ring
      .map(([lon, lat], i) => {
        const [x, y] = project(lat, lon);
        return `${i === 0 ? "M" : "L"} ${x.toFixed(2)} ${y.toFixed(2)}`;
      })
      .join(" ") + " Z"
  );
}

function lineToPath(coords) {
  if (!coords || coords.length === 0) return "";
  return coords
    .map(([lon, lat], i) => {
      const [x, y] = project(lat, lon);
      return `${i === 0 ? "M" : "L"} ${x.toFixed(2)} ${y.toFixed(2)}`;
    })
    .join(" ");
}

function geometryToPath(geom) {
  if (!geom || !geom.type) return "";
  const { type, coordinates } = geom;
  if (type === "Polygon") return (coordinates || []).map(ringToPath).join(" ");
  if (type === "MultiPolygon") return (coordinates || []).flat().map(ringToPath).join(" ");
  if (type === "LineString") return lineToPath(coordinates);
  if (type === "MultiLineString") return (coordinates || []).map(lineToPath).join(" ");
  return "";
}

// Illustrative coastline silhouette -- NOT survey-grade. This is now only
// a FALLBACK: at startup the map fetches GET /api/geo and renders the real
// India coastline (scripts/fetch_real_coastline.py) when present, labeled
// with its source; this path appears solely while no real geometry exists,
// and the provenance box always says so.
const COASTLINE_PATH =
  "M 210,40 C 190,90 175,140 165,190 C 155,250 148,300 145,340 " +
  "C 142,380 150,410 165,435 C 180,460 195,480 205,505 " +
  "C 212,525 215,545 222,560 C 228,572 238,578 248,570 " +
  "C 255,560 258,545 262,528 C 268,500 278,478 295,462 " +
  "C 320,440 355,430 385,435 C 415,440 445,455 470,445 " +
  "C 495,435 505,410 520,388 C 535,365 555,350 575,340 " +
  "C 595,330 605,310 598,288 C 590,262 570,245 555,225 " +
  "C 545,210 542,190 548,172 C 555,150 560,128 550,108 " +
  "C 540,88 518,78 498,72 C 470,64 440,62 410,58 " +
  "C 370,52 330,48 290,44 C 265,42 235,40 210,40 Z";

function MapPanel({ region, advisories, checkPoint, boundarySafe, isThinking }) {
  const center = REGIONS[region] || REGIONS["Kerala coast"];
  const [cx, cy] = project(center.lat, center.lon);
  const focusPoint = checkPoint ? project(checkPoint.lat, checkPoint.lon) : null;

  // Real map geometry from the backend (GET /api/geo): coastline fetched
  // by scripts/fetch_real_coastline.py, the exact EEZ polygon the
  // guardrail checks, and the region display boxes. Every layer arrives
  // with its own source label, so the UI can show provenance honestly and
  // fall back to the labeled silhouette when no real coastline exists yet.
  const [geo, setGeo] = useState(null);
  useEffect(() => {
    let cancelled = false;
    fetch(`${API_BASE}/api/geo`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((data) => !cancelled && setGeo(data))
      .catch(() => !cancelled && setGeo({ error: true }));
    return () => {
      cancelled = true;
    };
  }, []);

  const hasRealCoastline = (geo?.coastline?.geometries || []).length > 0;
  const coastlineLabel = !geo
    ? "loading…"
    : hasRealCoastline
      ? geo.coastline.label
      : "approx. silhouette (run scripts/fetch_real_coastline.py)";
  const eezLabel = !geo
    ? "loading…"
    : (geo?.eez?.geometries || []).length > 0
      ? geo.eez.is_placeholder
        ? "placeholder [DEMO DATA] (run scripts/fetch_real_eez.py)"
        : geo.eez.label
      : "unavailable";

  return (
    <div style={{ position: "relative", width: "100%", height: "100%", background: T.bg, overflow: "hidden" }}>
      <svg viewBox={`0 0 ${MAP_W} ${MAP_H}`} style={{ width: "100%", height: "100%", display: "block" }} preserveAspectRatio="xMidYMid meet">
        <defs>
          <radialGradient id="oceanGrad" cx="50%" cy="40%" r="75%">
            <stop offset="0%" stopColor="#0D2233" />
            <stop offset="100%" stopColor="#081420" />
          </radialGradient>
          <radialGradient id="focusGlow" cx="50%" cy="50%" r="50%">
            <stop offset="0%" stopColor={T.cyan} stopOpacity="0.35" />
            <stop offset="100%" stopColor={T.cyan} stopOpacity="0" />
          </radialGradient>
          <pattern id="grid" width="32" height="32" patternUnits="userSpaceOnUse">
            <path d="M 32 0 L 0 0 0 32" fill="none" stroke={T.border} strokeWidth="0.5" opacity="0.4" />
          </pattern>
        </defs>

        <rect width={MAP_W} height={MAP_H} fill="url(#oceanGrad)" />
        <rect width={MAP_W} height={MAP_H} fill="url(#grid)" />

        <g style={{ transition: "transform 900ms cubic-bezier(0.22, 1, 0.36, 1)" }} transform={`translate(${cx}, ${cy})`}>
          <circle r="90" fill="url(#focusGlow)" />
        </g>

        {/* EEZ boundary: the exact polygon the backend guardrail checks
            against. Dashed cyan; the provenance box labels it real vs
            placeholder. */}
        {(geo?.eez?.geometries || []).map((geom, i) => (
          <path
            key={`eez-${i}`}
            d={geometryToPath(geom)}
            fill="none"
            stroke={T.cyan}
            strokeWidth="1"
            strokeDasharray="5 4"
            opacity="0.5"
          />
        ))}

        {/* Real India coastline when fetched; the bundled illustrative
            silhouette only ever appears as a labeled fallback. */}
        {hasRealCoastline ? (
          geo.coastline.geometries.map((geom, i) => (
            <path
              key={`land-${i}`}
              d={geometryToPath(geom)}
              fill="#0B1B28"
              stroke={T.borderBright}
              strokeWidth="1.5"
              opacity="0.9"
            />
          ))
        ) : (
          <path d={COASTLINE_PATH} fill="#0B1B28" stroke={T.borderBright} strokeWidth="1.5" opacity="0.9" />
        )}

        {/* Approximate display boxes for the five coastal regions -- the
            selected one is highlighted. Dashed = display extent, not a
            legal boundary. */}
        {Object.entries(geo?.regions || {}).map(([name, b]) => {
          const [x1, y1] = project(b.lat_max, b.lon_min);
          const [x2, y2] = project(b.lat_min, b.lon_max);
          const active = name.toLowerCase() === region.toLowerCase();
          return (
            <rect
              key={name}
              x={x1}
              y={y1}
              width={x2 - x1}
              height={y2 - y1}
              fill={active ? "rgba(94, 200, 216, 0.05)" : "none"}
              stroke={active ? T.cyan : T.textFaint}
              strokeWidth={active ? 1.1 : 0.7}
              strokeDasharray="3 4"
              opacity={active ? 0.8 : 0.35}
            />
          );
        })}

        {[8, 12, 16, 20, 24].map((lat) => {
          const [, y] = project(lat, LON_MIN);
          return (
            <g key={lat}>
              <line x1={0} y1={y} x2={12} y2={y} stroke={T.textFaint} strokeWidth="1" />
              <text x={16} y={y + 3} fontFamily="'IBM Plex Mono', monospace" fontSize="9" fill={T.textFaint}>
                {lat}°N
              </text>
            </g>
          );
        })}

        {advisories.map((a, i) => {
          const [x, y] = project(a.location.lat, a.location.lon);
          const color = a.quality === "high" ? T.cyan : a.quality === "moderate" ? T.amber : T.textDim;
          const zone = Array.isArray(a.zone_polygon) && a.zone_polygon.length >= 3;
          return (
            <g key={i} style={{ animation: `fadeInPin 500ms ease-out ${i * 90}ms both` }}>
              {/* Non-uniform fishing zone: a corridor drawn around the
                  actual SST front the backend detected -- every zone has
                  its own shape/orientation, never a uniform box. */}
              {zone && (
                <path
                  d={ringToPath(a.zone_polygon.map((p) => [p.lon, p.lat]))}
                  fill={color}
                  fillOpacity="0.15"
                  stroke={color}
                  strokeWidth="1.2"
                  strokeDasharray="4 3"
                />
              )}
              <circle cx={x} cy={y} r={a.quality === "high" ? 9 : 6.5} fill={color} opacity="0.18" />
              <circle cx={x} cy={y} r={a.quality === "high" ? 5 : 3.5} fill={color} opacity="0.9" />
              {a.quality === "high" && (
                <circle cx={x} cy={y} r="5" fill="none" stroke={color} strokeWidth="1.5" opacity="0.6">
                  <animate attributeName="r" values="5;16;5" dur="2.4s" repeatCount="indefinite" />
                  <animate attributeName="opacity" values="0.6;0;0.6" dur="2.4s" repeatCount="indefinite" />
                </circle>
              )}
            </g>
          );
        })}

        {focusPoint && (
          <g transform={`translate(${focusPoint[0]}, ${focusPoint[1]})`}>
            <circle r="14" fill="none" stroke={boundarySafe ? T.green : T.red} strokeWidth="2" opacity="0.7" />
            <line x1="-9" y1="0" x2="9" y2="0" stroke={boundarySafe ? T.green : T.red} strokeWidth="1.5" />
            <line x1="0" y1="-9" x2="0" y2="9" stroke={boundarySafe ? T.green : T.red} strokeWidth="1.5" />
          </g>
        )}

        {isThinking && (
          <g transform={`translate(${cx}, ${cy})`}>
            <circle r="4" fill={T.cyan}>
              <animate attributeName="r" values="4;120" dur="1.8s" repeatCount="indefinite" />
              <animate attributeName="opacity" values="0.7;0" dur="1.8s" repeatCount="indefinite" />
            </circle>
          </g>
        )}
      </svg>

      <style>{`
        @keyframes fadeInPin {
          from { opacity: 0; transform: scale(0.4); transform-origin: center; }
          to { opacity: 1; transform: scale(1); }
        }
      `}</style>

      <div
        style={{
          position: "absolute", bottom: 20, left: 20, display: "flex", gap: 16,
          fontFamily: "'IBM Plex Mono', monospace", fontSize: 11, color: T.textDim,
          background: "rgba(15, 31, 48, 0.85)", padding: "10px 14px", borderRadius: 4,
          border: `1px solid ${T.border}`,
        }}
      >
        {[
          { label: "high PFZ", color: T.cyan },
          { label: "moderate", color: T.amber },
          { label: "low", color: T.textDim },
        ].map((l) => (
          <div key={l.label} style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <div style={{ width: 8, height: 8, borderRadius: "50%", background: l.color }} />
            {l.label}
          </div>
        ))}
      </div>

      {/* Data-source provenance for the map itself -- real vs placeholder,
          always labeled (same honesty standard as the backend). */}
      <div
        style={{
          position: "absolute", bottom: 20, right: 20, maxWidth: 280, textAlign: "right",
          fontFamily: "'IBM Plex Mono', monospace", fontSize: 9.5, color: T.textFaint,
          background: "rgba(15, 31, 48, 0.85)", padding: "8px 10px", borderRadius: 4,
          border: `1px solid ${T.border}`, lineHeight: 1.6, letterSpacing: "0.02em",
        }}
      >
        <div>coastline: {coastlineLabel}</div>
        <div>EEZ: {eezLabel}</div>
        <div>fishing zones: ±5 km corridor around detected SST fronts (derived)</div>
      </div>

      <div
        style={{
          position: "absolute", top: 20, left: 20,
          fontFamily: "'IBM Plex Mono', monospace", fontSize: 12, color: T.cyan, letterSpacing: "0.02em",
        }}
      >
        {region.toUpperCase()} · {center.lat.toFixed(2)}°N, {center.lon.toFixed(2)}°E
      </div>
    </div>
  );
}

// ============================================================================
// AGENT TRACE — animates through the trace[] array the backend returns.
// NOTE: the backend runs the pipeline synchronously and returns the full
// trace at once (see PipelineResponse.trace in app/models/schemas.py) --
// this is NOT a live stream from the server. We replay it client-side at a
// fixed pace so the sequence is visible, rather than dumping it all at once.
// If true step-by-step streaming matters later, the backend would need a
// WebSocket/SSE endpoint instead of a single POST /api/query.
// ============================================================================
function AgentTracePanel({ trace, revealCount }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 1 }}>
      {trace.map((step, i) => {
        const revealed = i < revealCount;
        const isLatest = i === revealCount - 1;
        return (
          <div
            key={i}
            style={{
              display: "flex", alignItems: "center", gap: 10, padding: "7px 10px",
              borderLeft: `2px solid ${isLatest ? T.cyan : "transparent"}`,
              background: isLatest ? "rgba(94, 200, 216, 0.06)" : "transparent",
              opacity: revealed ? 1 : 0,
              transform: revealed ? "translateY(0)" : "translateY(4px)",
              transition: "opacity 250ms ease, transform 250ms ease",
            }}
          >
            <div
              style={{
                width: 6, height: 6, borderRadius: "50%",
                background: isLatest ? T.cyan : T.green, flexShrink: 0,
                boxShadow: isLatest ? `0 0 8px ${T.cyan}` : "none",
              }}
            />
            <span style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 12, color: T.text }}>
              {step.agent_name}
            </span>
          </div>
        );
      })}
    </div>
  );
}

// ============================================================================
// CHAT MESSAGE
// ============================================================================
// ============================================================================
// PROVENANCE BADGES -- expandable source chips for the weather (Open-Meteo) and
// ocean (NOAA CoastWatch ERDDAP) notes the backend returns in map_layers. Purely
// presentational; the full provenance text is rendered verbatim from the
// API response.
// ============================================================================
function ProvenanceBadges({ weatherNotes, oceanNotes }) {
  const [open, setOpen] = useState(null);
  const items = [
    weatherNotes && { key: "weather", label: "Open-Meteo weather sources", text: weatherNotes },
    oceanNotes && { key: "ocean", label: "satellite ocean sources", text: oceanNotes },
  ].filter(Boolean);
  if (items.length === 0) return null;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 5, marginTop: 6, maxWidth: "84%" }}>
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
        {items.map((it) => (
          <button
            key={it.key}
            onClick={() => setOpen(open === it.key ? null : it.key)}
            style={{
              fontFamily: "'IBM Plex Mono', monospace", fontSize: 9.5, letterSpacing: "0.03em",
              padding: "3px 8px", borderRadius: 3, cursor: "pointer",
              background: open === it.key ? "rgba(94, 200, 216, 0.10)" : "transparent",
              color: open === it.key ? T.cyan : T.textFaint,
              border: `1px solid ${open === it.key ? T.cyan : T.border}`,
            }}
          >
            ◉ {it.label}
          </button>
        ))}
      </div>
      {items
        .filter((it) => it.key === open)
        .map((it) => (
          <div
            key={it.key}
            style={{
              fontFamily: "'IBM Plex Mono', monospace", fontSize: 10.5, lineHeight: 1.6,
              color: T.textDim, background: T.panel, border: `1px solid ${T.border}`,
              borderRadius: 4, padding: "8px 10px", maxHeight: 150, overflowY: "auto",
              whiteSpace: "pre-wrap", wordBreak: "break-word",
            }}
          >
            {it.text}
          </div>
        ))}
    </div>
  );
}

function ChatMessage({ role, content, riskLevel, isAlert, weatherNotes, oceanNotes }) {
  const isUser = role === "user";
  const bubble = (
      <div
        style={{
          maxWidth: "84%", padding: "10px 14px",
          borderRadius: isUser ? "10px 10px 2px 10px" : "10px 10px 10px 2px",
          background: isUser ? T.elevated : isAlert ? "rgba(232, 101, 77, 0.1)" : T.panelRaised,
          border: `1px solid ${isUser ? T.borderBright : isAlert ? T.red : T.border}`,
          fontFamily: "'IBM Plex Sans', sans-serif", fontSize: 13.5, lineHeight: 1.55, color: T.text,
        }}
      >
        {content}
        {riskLevel && (
          <div
            style={{
              marginTop: 8, display: "inline-flex", alignItems: "center", gap: 6,
              fontFamily: "'IBM Plex Mono', monospace", fontSize: 10, padding: "3px 8px", borderRadius: 3,
              background: `${RISK_COLOR[riskLevel]}18`, color: RISK_COLOR[riskLevel],
              border: `1px solid ${RISK_COLOR[riskLevel]}40`,
            }}
          >
            <div style={{ width: 5, height: 5, borderRadius: "50%", background: RISK_COLOR[riskLevel] }} />
            risk: {riskLevel}
          </div>
        )}
      </div>
  );

  return (
    <div
      style={{
        display: "flex", flexDirection: "column",
        alignItems: isUser ? "flex-end" : "flex-start", marginBottom: 14,
      }}
    >
      {bubble}
      {!isUser && (weatherNotes || oceanNotes) && (
        <ProvenanceBadges weatherNotes={weatherNotes} oceanNotes={oceanNotes} />
      )}
    </div>
  );
}

// ============================================================================
// MAIN APP
// ============================================================================
export default function App() {
  const [region, setRegion] = useState("Kerala coast");
  const [messages, setMessages] = useState([
    {
      role: "assistant",
      content:
        "Command-center online. Ask about fishing conditions, weather, or safety for any coastal region — I'll pull ocean data, run the boundary check, and assess risk before responding.",
    },
  ]);
  const [input, setInput] = useState("");
  const [isRunning, setIsRunning] = useState(false);
  const [trace, setTrace] = useState([]);
  const [revealCount, setRevealCount] = useState(0);
  const [advisories, setAdvisories] = useState([]);
  const [checkPoint, setCheckPoint] = useState(null);
  const [boundarySafe, setBoundarySafe] = useState(true);
  const [connectionError, setConnectionError] = useState(null);
  const scrollRef = useRef(null);
  const revealTimerRef = useRef(null);

  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [messages]);

  useEffect(() => () => clearInterval(revealTimerRef.current), []);

  const runQuery = useCallback(
    async (query) => {
      if (!query.trim() || isRunning) return;
      setMessages((m) => [...m, { role: "user", content: query }]);
      setInput("");
      setIsRunning(true);
      setConnectionError(null);
      setTrace([]);
      setRevealCount(0);

      try {
        const res = await fetch(`${API_BASE}/api/query`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ query }),
        });

        if (!res.ok) {
          // Backend has no mock/fallback mode -- a non-200 here means a
          // real agent or fetcher genuinely failed (bad API key, rate
          // limit, network issue, malformed LLM output, etc). FastAPI's
          // default error shape is {"detail": "..."} for unhandled
          // exceptions; surface that exact message rather than just the
          // status code, since it names which agent failed and why.
          let detail = `Backend returned ${res.status}`;
          try {
            const errBody = await res.json();
            if (errBody?.detail) detail = errBody.detail;
          } catch {
            // response wasn't JSON -- fall back to the status-only message
          }
          throw new Error(detail);
        }

        const data = await res.json();

        // Replay the trace client-side at a fixed pace (see AgentTracePanel
        // note above -- the backend returns it all at once, not streamed).
        setTrace(data.trace || []);
        const totalSteps = (data.trace || []).length;
        let revealed = 0;
        revealTimerRef.current = setInterval(() => {
          revealed += 1;
          setRevealCount(revealed);
          if (revealed >= totalSteps) {
            clearInterval(revealTimerRef.current);

            const region_ = data.map_layers?.region || region;
            const centroid = data.map_layers?.centroid;
            const pfz = data.map_layers?.pfz_advisories || [];

            setMessages((m) => [
              ...m,
              {
                role: "assistant",
                content: data.chat_response,
                riskLevel: data.risk_assessment?.risk_level || null,
                isAlert: data.safety_alert,
                weatherNotes: data.map_layers?.weather_notes || null,
                oceanNotes: data.map_layers?.ocean_notes || null,
              },
            ]);
            if (region_) setRegion(region_);
            setAdvisories(pfz);
            setCheckPoint(centroid || null);
            setBoundarySafe(!data.safety_alert);
            setIsRunning(false);
          }
        }, 280);
      } catch (err) {
        const isNetworkError = err.message === "Failed to fetch";
        const displayMessage = isNetworkError
          ? `Can't reach the backend at ${API_BASE}. Is it running? (uvicorn app.main:app --reload)`
          : err.message;

        if (isNetworkError) {
          // Backend unreachable entirely -- show as a persistent banner.
          setConnectionError(displayMessage);
        } else {
          // Backend reachable but a real agent/fetcher call failed for
          // this specific query -- show it as a chat message so it's
          // clear the failure was query-specific, not "the app is broken."
          setMessages((m) => [
            ...m,
            { role: "assistant", content: `Query failed: ${displayMessage}`, isAlert: true },
          ]);
        }
        setTrace([]);
        setRevealCount(0);
        setIsRunning(false);
      }
    },
    [isRunning, region]
  );

  const quickPrompts = ["Is it safe to fish today?", "Show me high-quality zones", "What's the weather looking like?"];

  return (
    <div
      style={{
        width: "100%", height: "100vh", minHeight: 640, display: "flex",
        background: T.bg, fontFamily: "'IBM Plex Sans', sans-serif", color: T.text,
      }}
    >
      <div
        style={{
          width: 400, minWidth: 360, display: "flex", flexDirection: "column",
          borderRight: `1px solid ${T.border}`, background: T.panel,
        }}
      >
        <div style={{ padding: "16px 18px", borderBottom: `1px solid ${T.border}` }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 10 }}>
            <div
              style={{
                width: 7, height: 7, borderRadius: "50%",
                background: connectionError ? T.red : T.green,
                boxShadow: `0 0 6px ${connectionError ? T.red : T.green}`,
              }}
            />
            <span style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 11, color: T.textDim, letterSpacing: "0.03em" }}>
              SIH26176 · MARINE INTELLIGENCE
            </span>
          </div>
          <select
            value={region}
            onChange={(e) => setRegion(e.target.value)}
            style={{
              width: "100%", background: T.panelRaised, border: `1px solid ${T.border}`, borderRadius: 5,
              color: T.text, fontFamily: "'IBM Plex Sans', sans-serif", fontSize: 13, padding: "7px 10px", outline: "none",
            }}
          >
            {Object.keys(REGIONS).map((r) => (
              <option key={r} value={r}>{r}</option>
            ))}
          </select>
        </div>

        {connectionError && (
          <div
            style={{
              margin: "12px 18px 0", padding: "10px 12px", borderRadius: 6,
              background: "rgba(232, 101, 77, 0.1)", border: `1px solid ${T.red}`,
              fontFamily: "'IBM Plex Mono', monospace", fontSize: 11.5, color: T.red, lineHeight: 1.5,
            }}
          >
            {connectionError}
          </div>
        )}

        <div ref={scrollRef} style={{ flex: 1, overflowY: "auto", padding: "16px 18px" }}>
          {messages.map((m, i) => (
            <ChatMessage
              key={i}
              role={m.role}
              content={m.content}
              riskLevel={m.riskLevel}
              isAlert={m.isAlert}
              weatherNotes={m.weatherNotes}
              oceanNotes={m.oceanNotes}
            />
          ))}
          {isRunning && (
            <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "6px 0", marginBottom: 10 }}>
              <div style={{ display: "flex", gap: 3 }}>
                {[0, 1, 2].map((i) => (
                  <div
                    key={i}
                    style={{
                      width: 5, height: 5, borderRadius: "50%", background: T.cyan,
                      animation: `bounce 1.1s ease-in-out ${i * 0.15}s infinite`,
                    }}
                  />
                ))}
              </div>
              <span style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 11, color: T.textDim }}>
                pipeline running
              </span>
            </div>
          )}
        </div>

        <div style={{ borderTop: `1px solid ${T.border}`, padding: "12px 10px", maxHeight: 260, overflowY: "auto" }}>
          <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 10, color: T.textFaint, letterSpacing: "0.04em", padding: "0 8px 8px" }}>
            AGENT PIPELINE {trace.length > 0 && `(${revealCount}/${trace.length})`}
          </div>
          {trace.length > 0 ? (
            <AgentTracePanel trace={trace} revealCount={revealCount} />
          ) : (
            <div style={{ padding: "8px 10px", fontFamily: "'IBM Plex Mono', monospace", fontSize: 11, color: T.textFaint }}>
              awaiting query
            </div>
          )}
        </div>

        <div style={{ padding: "10px 18px", display: "flex", flexWrap: "wrap", gap: 6, borderTop: `1px solid ${T.border}` }}>
          {quickPrompts.map((p) => (
            <button
              key={p}
              onClick={() => runQuery(p)}
              disabled={isRunning}
              style={{
                fontFamily: "'IBM Plex Sans', sans-serif", fontSize: 11.5, color: T.textDim,
                background: "transparent", border: `1px solid ${T.border}`, borderRadius: 20,
                padding: "5px 12px", cursor: isRunning ? "default" : "pointer", opacity: isRunning ? 0.5 : 1,
              }}
            >
              {p}
            </button>
          ))}
        </div>

        <div style={{ padding: "12px 18px 18px", display: "flex", gap: 8 }}>
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && runQuery(input)}
            placeholder="Ask about conditions, safety, PFZ..."
            disabled={isRunning}
            style={{
              flex: 1, background: T.panelRaised, border: `1px solid ${T.border}`, borderRadius: 6,
              color: T.text, fontFamily: "'IBM Plex Sans', sans-serif", fontSize: 13, padding: "9px 12px", outline: "none",
            }}
          />
          <button
            onClick={() => runQuery(input)}
            disabled={isRunning || !input.trim()}
            style={{
              background: input.trim() && !isRunning ? T.cyan : T.elevated, border: "none", borderRadius: 6,
              color: input.trim() && !isRunning ? T.bg : T.textFaint, fontFamily: "'IBM Plex Sans', sans-serif",
              fontSize: 13, fontWeight: 600, padding: "9px 16px", cursor: input.trim() && !isRunning ? "pointer" : "default",
            }}
          >
            Send
          </button>
        </div>
      </div>

      <div style={{ flex: 1, position: "relative" }}>
        <MapPanel region={region} advisories={advisories} checkPoint={checkPoint} boundarySafe={boundarySafe} isThinking={isRunning} />
      </div>

      <style>{`
        @keyframes bounce {
          0%, 60%, 100% { transform: translateY(0); opacity: 0.5; }
          30% { transform: translateY(-3px); opacity: 1; }
        }
        ::-webkit-scrollbar { width: 6px; }
        ::-webkit-scrollbar-track { background: transparent; }
        ::-webkit-scrollbar-thumb { background: ${T.border}; border-radius: 3px; }
      `}</style>
    </div>
  );
}
