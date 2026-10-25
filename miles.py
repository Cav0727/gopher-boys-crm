"""Route miles: Pooler base → supplier → jobsite.

Prefer OSRM public demo for driving distance. Fallback: Nominatim geocode +
haversine × ROAD_FACTOR. Geocodes cached in SQLite. Respects Nominatim
usage policy (User-Agent + ≥1s between requests).

Address suggestions use Photon (Komoot) for free typeahead, biased to
Coastal Georgia / Pooler.
"""

from __future__ import annotations

import json
import math
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from db import db, utcnow
from suppliers import (
    BASE_ADDRESS,
    BASE_LAT,
    BASE_LON,
    full_address,
    get_supplier,
)

USER_AGENT = "GopherBoysCRM/1.0 (material-pickup; contact=mack@ridgewayroofingco.com)"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
PHOTON_URL = "https://photon.komoot.io/api/"
OSRM_URL = "https://router.project-osrm.org/route/v1/driving"
ROAD_FACTOR = 1.30  # approx road miles from great-circle when OSRM unavailable

# Coastal GA / Savannah metro viewbox (left, top, right, bottom) for Nominatim
COASTAL_GA_VIEWBOX = "-81.65,32.45,-80.70,31.85"
# US state abbreviations (+ DC) used to detect whether an address already has a state
_STATE_RE = re.compile(
    r"\b("
    r"AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|IA|ID|IL|IN|KS|KY|LA|MA|MD|ME|MI|MN|MO|"
    r"MS|MT|NC|ND|NE|NH|NJ|NM|NV|NY|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VA|VT|WA|WI|"
    r"WV|WY|DC"
    r")\b"
    r"|\bGeorgia\b|\bSouth Carolina\b|\bUSA\b|\bUnited States\b",
    re.I,
)

_last_nominatim_at = 0.0
# Short in-memory cache for Photon suggestions (query → (expires, results))
_suggest_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_SUGGEST_TTL = 300.0  # seconds


def ensure_geocode_cache_table() -> None:
    with db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS geocode_cache (
                query TEXT PRIMARY KEY,
                lat REAL NOT NULL,
                lon REAL NOT NULL,
                source TEXT,
                created_at TEXT NOT NULL
            )
            """
        )


def _http_get_json(url: str, timeout: float = 12.0) -> Any | None:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError, ValueError):
        return None


def _nominatim_throttle() -> None:
    global _last_nominatim_at
    elapsed = time.time() - _last_nominatim_at
    if elapsed < 1.05:
        time.sleep(1.05 - elapsed)
    _last_nominatim_at = time.time()


def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 3958.8  # Earth radius miles
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def bias_address_query(query: str) -> str:
    """Append Coastal GA / USA when the free-text address has no state."""
    q = (query or "").strip()
    if not q:
        return q
    if _STATE_RE.search(q):
        return q
    return f"{q}, GA, USA"


def geocode(query: str) -> tuple[float, float] | None:
    """Return (lat, lon) for an address string, using SQLite cache + Nominatim.

    Biases toward Coastal Georgia (viewbox + append ', GA, USA' when no state).
    """
    q = (query or "").strip()
    if not q:
        return None
    ensure_geocode_cache_table()
    with db() as conn:
        row = conn.execute(
            "SELECT lat, lon FROM geocode_cache WHERE query = ? COLLATE NOCASE",
            (q,),
        ).fetchone()
        if row:
            return float(row["lat"]), float(row["lon"])

    biased = bias_address_query(q)
    # Also check cache under the biased form
    if biased != q:
        with db() as conn:
            row = conn.execute(
                "SELECT lat, lon FROM geocode_cache WHERE query = ? COLLATE NOCASE",
                (biased,),
            ).fetchone()
            if row:
                lat, lon = float(row["lat"]), float(row["lon"])
                with db() as conn2:
                    conn2.execute(
                        """
                        INSERT OR REPLACE INTO geocode_cache
                            (query, lat, lon, source, created_at)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (q, lat, lon, "nominatim", utcnow()),
                    )
                return lat, lon

    _nominatim_throttle()
    params = urllib.parse.urlencode(
        {
            "q": biased,
            "format": "json",
            "limit": 1,
            "countrycodes": "us",
            "viewbox": COASTAL_GA_VIEWBOX,
            "bounded": 0,
        }
    )
    data = _http_get_json(f"{NOMINATIM_URL}?{params}")
    if not data or not isinstance(data, list) or not data:
        return None
    try:
        lat = float(data[0]["lat"])
        lon = float(data[0]["lon"])
    except (KeyError, TypeError, ValueError):
        return None

    with db() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO geocode_cache (query, lat, lon, source, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (q, lat, lon, "nominatim", utcnow()),
        )
        if biased != q:
            conn.execute(
                """
                INSERT OR REPLACE INTO geocode_cache (query, lat, lon, source, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (biased, lat, lon, "nominatim", utcnow()),
            )
    return lat, lon


def _photon_props_to_suggestion(feat: dict[str, Any]) -> dict[str, Any] | None:
    props = feat.get("properties") or {}
    geom = feat.get("geometry") or {}
    coords = geom.get("coordinates") or []
    lon = lat = None
    if len(coords) >= 2:
        try:
            lon = float(coords[0])
            lat = float(coords[1])
        except (TypeError, ValueError):
            lon = lat = None

    housenumber = (props.get("housenumber") or "").strip()
    street = (props.get("street") or props.get("name") or "").strip()
    city = (
        props.get("city")
        or props.get("town")
        or props.get("village")
        or props.get("municipality")
        or props.get("county")
        or ""
    ).strip()
    state = (props.get("state") or "").strip()
    # Photon often returns full state name; normalize common ones
    state_map = {
        "georgia": "GA",
        "south carolina": "SC",
        "florida": "FL",
        "north carolina": "NC",
        "alabama": "AL",
        "tennessee": "TN",
    }
    state_abbr = state_map.get(state.lower(), state)
    if len(state_abbr) > 2 and state_abbr.upper() in {
        "GA", "SC", "FL", "NC", "AL", "TN"
    }:
        state_abbr = state_abbr.upper()
    # Keep 2-letter if already abbr
    if len(state) == 2:
        state_abbr = state.upper()

    postcode = (props.get("postcode") or "").strip()
    country = (props.get("country") or props.get("countrycode") or "").strip()

    if housenumber and street and not street.lower().startswith(housenumber.lower()):
        street_line = f"{housenumber} {street}"
    elif street:
        street_line = street
    elif props.get("name"):
        street_line = str(props["name"]).strip()
    else:
        street_line = ""

    parts = [p for p in (street_line, city, state_abbr, postcode) if p]
    if not parts:
        return None
    # Prefer US / Coastal area results; still return others if Photon ranked them
    display_parts = [street_line] if street_line else []
    city_state = ", ".join(p for p in (city, state_abbr) if p)
    if city_state:
        display_parts.append(city_state)
    if postcode:
        if display_parts:
            display_parts[-1] = f"{display_parts[-1]} {postcode}".strip()
        else:
            display_parts.append(postcode)
    display = ", ".join(display_parts)

    # Full formatted address for geocoding / miles
    formatted = display
    if country and country.upper() not in ("US", "USA", "UNITED STATES") and "USA" not in formatted.upper():
        # still US-biased; Photon countrycode is often "US"
        pass
    if state_abbr and "USA" not in formatted.upper() and country.upper() in ("", "US", "USA", "UNITED STATES"):
        formatted = f"{formatted}, USA" if not formatted.endswith("USA") else formatted

    return {
        "display": display,
        "street": street_line,
        "city": city,
        "state": state_abbr,
        "zip": postcode,
        "lat": lat,
        "lon": lon,
        "formatted": formatted.rstrip(", "),
    }


def suggest_addresses(query: str, limit: int = 6) -> list[dict[str, Any]]:
    """Free address typeahead via Photon, biased to Pooler / Coastal GA."""
    q = (query or "").strip()
    if len(q) < 3:
        return []
    limit = max(1, min(int(limit or 6), 10))
    cache_key = f"{q.lower()}|{limit}"
    now = time.time()
    cached = _suggest_cache.get(cache_key)
    if cached and cached[0] > now:
        return cached[1]

    params = urllib.parse.urlencode(
        {
            "q": q,
            "lat": BASE_LAT,
            "lon": BASE_LON,
            "limit": limit,
            "lang": "en",
        }
    )
    data = _http_get_json(f"{PHOTON_URL}?{params}", timeout=8.0)
    results: list[dict[str, Any]] = []
    if data and isinstance(data, dict):
        for feat in data.get("features") or []:
            sug = _photon_props_to_suggestion(feat)
            if sug:
                results.append(sug)

    # Prefer GA / SC (Coastal) results first without dropping others
    def _rank(s: dict[str, Any]) -> tuple[int, int]:
        st = (s.get("state") or "").upper()
        if st in ("GA", "GEORGIA"):
            return (0, 0)
        if st in ("SC", "SOUTH CAROLINA"):
            return (1, 0)
        return (2, 0)

    results.sort(key=_rank)
    _suggest_cache[cache_key] = (now + _SUGGEST_TTL, results)
    # Bound cache size
    if len(_suggest_cache) > 200:
        expired = [k for k, (exp, _) in _suggest_cache.items() if exp <= now]
        for k in expired:
            _suggest_cache.pop(k, None)
    return results


def osrm_driving_miles(points: list[tuple[float, float]]) -> float | None:
    """One-way driving miles through waypoints via public OSRM demo."""
    if len(points) < 2:
        return None
    coords = ";".join(f"{lon},{lat}" for lat, lon in points)
    url = f"{OSRM_URL}/{coords}?overview=false&alternatives=false"
    data = _http_get_json(url, timeout=15.0)
    if not data or data.get("code") != "Ok":
        return None
    routes = data.get("routes") or []
    if not routes:
        return None
    meters = routes[0].get("distance")
    if meters is None:
        return None
    return round(float(meters) / 1609.344, 1)


def approx_road_miles(points: list[tuple[float, float]]) -> float:
    total = 0.0
    for i in range(len(points) - 1):
        total += haversine_miles(*points[i], *points[i + 1])
    return round(total * ROAD_FACTOR, 1)


def resolve_point(
    address: str | None = None,
    lat: float | None = None,
    lon: float | None = None,
) -> tuple[float, float] | None:
    if lat is not None and lon is not None:
        try:
            return float(lat), float(lon)
        except (TypeError, ValueError):
            pass
    if address:
        return geocode(address)
    return None


def _parse_coord(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def calc_route_miles(
    supplier_id: str | None = None,
    supplier_address: str | None = None,
    jobsite_address: str | None = None,
    jobsite_lat: float | None = None,
    jobsite_lon: float | None = None,
    jobsite_city: str | None = None,
) -> dict[str, Any]:
    """
    Miles = Pooler base → supplier pickup → jobsite delivery (one-way total).

    Returns dict with miles, method, detail, ok, supplier_address, points.
    When jobsite_lat/lon are provided (e.g. from address autocomplete), those
    coords are used directly so miles do not depend on a second ambiguous geocode.
    """
    base = (BASE_LAT, BASE_LON)
    # Prefer geocoded base if cache has a better hit for the label
    cached_base = geocode(BASE_ADDRESS)
    if cached_base:
        base = cached_base

    supplier = get_supplier(supplier_id)
    pickup_addr = None
    pickup_lat = pickup_lon = None
    if supplier:
        pickup_addr = full_address(supplier)
        pickup_lat = supplier.get("lat")
        pickup_lon = supplier.get("lon")
    elif supplier_address:
        pickup_addr = supplier_address.strip()

    jobsite = (jobsite_address or "").strip()
    city = (jobsite_city or "").strip()
    # If city is separate and not already in the street line, append for geocode
    if jobsite and city and city.lower() not in jobsite.lower():
        jobsite_for_geo = f"{jobsite}, {city}"
    else:
        jobsite_for_geo = jobsite

    j_lat = _parse_coord(jobsite_lat)
    j_lon = _parse_coord(jobsite_lon)

    if not pickup_addr:
        return {
            "ok": False,
            "miles": None,
            "method": None,
            "detail": "Pick a supplier or enter a pickup address.",
            "supplier_address": None,
        }
    if not jobsite and (j_lat is None or j_lon is None):
        return {
            "ok": False,
            "miles": None,
            "method": None,
            "detail": "Enter the jobsite / delivery address.",
            "supplier_address": pickup_addr,
        }

    pickup = resolve_point(pickup_addr, pickup_lat, pickup_lon)
    dest = resolve_point(jobsite_for_geo or jobsite, j_lat, j_lon)
    if not pickup:
        return {
            "ok": False,
            "miles": None,
            "method": None,
            "detail": f"Could not locate supplier: {pickup_addr}",
            "supplier_address": pickup_addr,
        }
    if not dest:
        return {
            "ok": False,
            "miles": None,
            "method": None,
            "detail": f"Could not locate jobsite: {jobsite_for_geo or jobsite}",
            "supplier_address": pickup_addr,
        }

    points = [base, pickup, dest]
    driving = osrm_driving_miles(points)
    if driving is not None:
        return {
            "ok": True,
            "miles": driving,
            "method": "osrm",
            "detail": (
                f"Driving miles (OSRM): Pooler → supplier → jobsite = {driving} mi"
            ),
            "supplier_address": pickup_addr,
            "approx": False,
        }

    approx = approx_road_miles(points)
    return {
        "ok": True,
        "miles": approx,
        "method": "haversine_road_factor",
        "detail": (
            f"Approx road miles (straight-line × {ROAD_FACTOR}): "
            f"Pooler → supplier → jobsite = {approx} mi"
        ),
        "supplier_address": pickup_addr,
        "approx": True,
    }
