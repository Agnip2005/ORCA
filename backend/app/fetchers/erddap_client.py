"""
Generic ERDDAP griddap client (open data, no key).

Default server: NOAA CoastWatch West Coast Node (coastwatch.pfeg.noaa.gov/erddap)
which serves global satellite SST (NASA JPL MUR) and chlorophyll (VIIRS / MODIS)
with no registration. Any ERDDAP server works (set ERDDAP_BASE).

Because dataset layouts differ (an extra `altitude` axis, ascending vs descending
latitude, different variable names), this client INTROSPECTS a dataset through
ERDDAP's own `/info/<id>/index.json` and builds the griddap query from what it
finds, instead of hard-coding one layout.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

import httpx

from app.fetchers.http_util import get_json

logger = logging.getLogger(__name__)


class ERDDAPError(RuntimeError):
    pass


@dataclass
class DatasetInfo:
    dataset_id: str
    variable: str
    dims: list[str]                 # axis order of the variable, e.g. [time, altitude, latitude, longitude]
    lat_dim: str
    lon_dim: str
    time_dim: str
    lat_spacing: float              # signed degrees per index step
    lon_spacing: float
    title: str = ""
    institution: str = ""
    units: str = ""
    extra: dict = field(default_factory=dict)


def parse_info(dataset_id: str, info_json: dict, variable_hints: list[str]) -> DatasetInfo:
    """Parse ERDDAP /info/<id>/index.json into a DatasetInfo. Pure function."""
    table = info_json.get("table") or {}
    cols = table.get("columnNames") or []
    try:
        i_type, i_var, i_attr, i_val = (cols.index(c) for c in ("Row Type", "Variable Name", "Attribute Name", "Value"))
    except ValueError as exc:
        raise ERDDAPError(f"{dataset_id}: unexpected /info columns {cols}") from exc
    rows = table.get("rows") or []

    dim_rows = [r for r in rows if r[i_type] == "dimension"]
    var_rows = [r for r in rows if r[i_type] == "variable"]
    var_names = [r[i_var] for r in var_rows]
    variable = next((h for h in variable_hints if h in var_names), None)
    if variable is None:
        raise ERDDAPError(f"{dataset_id}: none of {variable_hints} in dataset variables {var_names}")

    # axis order for this variable comes from its 'variable' row's Value ("time, altitude, latitude, longitude")
    vrow = next(r for r in var_rows if r[i_var] == variable)
    dims = [d.strip() for d in str(vrow[i_val]).split(",") if d.strip()]
    if not dims:
        dims = [r[i_var] for r in dim_rows]

    spacing = {}
    for r in dim_rows:
        m = re.search(r"averageSpacing=(-?[0-9.]+(?:[eE][-+]?\d+)?)", str(r[i_val]))
        spacing[r[i_var]] = float(m.group(1)) if m else None

    def find(prefix: str) -> str:
        for d in dims:
            if d.lower().startswith(prefix):
                return d
        raise ERDDAPError(f"{dataset_id}: no '{prefix}*' axis in {dims}")

    lat_dim, lon_dim, time_dim = find("lat"), find("lon"), find("time")
    if not spacing.get(lat_dim) or not spacing.get(lon_dim):
        raise ERDDAPError(f"{dataset_id}: could not read lat/lon grid spacing from /info ({spacing})")

    def attr(var: str, name: str) -> str:
        for r in rows:
            if r[i_type] == "attribute" and r[i_var] == var and r[i_attr] == name:
                return str(r[i_val])
        return ""

    return DatasetInfo(
        dataset_id=dataset_id, variable=variable, dims=dims,
        lat_dim=lat_dim, lon_dim=lon_dim, time_dim=time_dim,
        lat_spacing=spacing[lat_dim], lon_spacing=spacing[lon_dim],
        title=attr("NC_GLOBAL", "title"), institution=attr("NC_GLOBAL", "institution"),
        units=attr(variable, "units"),
    )


def build_query(info: DatasetInfo, bounds: dict, target_step_deg: float, reverse_lat: bool = False) -> str:
    """Build the griddap constraint string, thinning the grid to ~target_step_deg."""
    lat_lo, lat_hi = bounds["lat_min"], bounds["lat_max"]
    lon_lo, lon_hi = bounds["lon_min"], bounds["lon_max"]
    descending = info.lat_spacing < 0
    if descending != reverse_lat:           # ERDDAP needs the range in axis order
        lat_lo, lat_hi = lat_hi, lat_lo
    lat_stride = max(1, round(target_step_deg / abs(info.lat_spacing)))
    lon_stride = max(1, round(target_step_deg / abs(info.lon_spacing)))

    parts = []
    for d in info.dims:
        if d == info.time_dim:
            parts.append("[(last)]")
        elif d == info.lat_dim:
            parts.append(f"[({lat_lo}):{lat_stride}:({lat_hi})]")
        elif d == info.lon_dim:
            parts.append(f"[({lon_lo}):{lon_stride}:({lon_hi})]")
        else:
            parts.append("[0]")            # altitude / depth singleton axis
    return info.variable + "".join(parts)


def parse_grid(info: DatasetInfo, data: dict) -> list[tuple[str, float, float, float]]:
    """ERDDAP JSON table -> [(time_iso, lat, lon, value)], nulls dropped. Pure function."""
    table = data.get("table") or {}
    cols = table.get("columnNames") or []
    try:
        it, ila, ilo, iv = (cols.index(info.time_dim), cols.index(info.lat_dim),
                            cols.index(info.lon_dim), cols.index(info.variable))
    except ValueError as exc:
        raise ERDDAPError(f"{info.dataset_id}: unexpected griddap columns {cols}") from exc
    out = []
    for row in table.get("rows") or []:
        v = row[iv]
        if v is None or (isinstance(v, float) and v != v):
            continue
        out.append((row[it], float(row[ila]), float(row[ilo]), float(v)))
    return out


class ERDDAPClient:
    def __init__(self, base: str, http_client: httpx.AsyncClient):
        self._base = base.rstrip("/")
        self._client = http_client
        self._info_cache: dict[tuple[str, str], DatasetInfo] = {}

    async def info(self, dataset_id: str, variable_hints: list[str]) -> DatasetInfo:
        key = (dataset_id, ",".join(variable_hints))
        if key not in self._info_cache:
            data = await get_json(self._client, f"{self._base}/info/{dataset_id}/index.json",
                                  what=f"ERDDAP info {dataset_id}")
            self._info_cache[key] = parse_info(dataset_id, data, variable_hints)
        return self._info_cache[key]

    async def fetch_grid(self, info: DatasetInfo, bounds: dict, target_step_deg: float):
        """Latest time slice over bounds. Retries once with lat range reversed if ERDDAP rejects the order."""
        last_err: Exception | None = None
        for reverse in (False, True):
            url = f"{self._base}/griddap/{info.dataset_id}.json"
            query = build_query(info, bounds, target_step_deg, reverse_lat=reverse)
            try:
                data = await get_json(self._client, f"{url}?{query}", what=f"ERDDAP griddap {info.dataset_id}")
                return parse_grid(info, data)
            except RuntimeError as exc:
                last_err = exc
                if "HTTP 4" not in str(exc):
                    raise
        raise ERDDAPError(f"{info.dataset_id}: griddap query rejected: {last_err}")
