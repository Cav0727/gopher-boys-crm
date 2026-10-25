"""Route miles: Pooler base → supplier → jobsite.

Prefer OSRM public demo for driving distance. Fallback: Nominatim geocode +
haversine × ROAD_FACTOR. Geocodes cached in SQLite. Respects Nominatim
usage policy (User-Agent + ≥1s between requests).

Address suggestions use Photon (Komoot) for free typeahead, filtered to
Coastal Georgia / near Pooler so far-away hits (NYC Park Ave, etc.) never
drive the miles quote.
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
# Reject geocode / suggestion / coords farther than this from Pooler base
SERVICE_RADIUS_MI = 90.0

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


def near_base(lat: float, lon: float, radius_mi: float = SERVICE_RADIUS_MI) -> bool:
    try:
        return haversine_miles(BASE_LAT, BASE_LON, float(lat), float(lon)) <= radius_mi
    except (TypeError, ValueError):
        return False


def bias_address_query(query: str) -> str:
    """Append Coastal GA / USA when the free-text address has no state."""
    q = (query or "").strip()
    if not q:
        return q
    if _STATE_RE.search(q):
        return q
    return f"{q}, GA, USA"


def normalize_state(state: str | None, default: str = "GA") -> str:
    """Return a 2-letter US state code; default GA for Coastal service area."""
    raw = (state or "").strip()
    if not raw:
        return default
    state_map = {
        "georgia": "GA",
        "south carolina": "SC",
        "florida": "FL",
        "north carolina": "NC",
        "alabama": "AL",
        "tennessee": "TN",
    }
    if len(raw) == 2:
        return raw.upper()
    return state_map.get(raw.lower(), raw.upper()[:2] if len(raw) >= 2 else default)


def compose_jobsite_query(
    street: str | None = None,
    city: str | None = None,
    state: str | None = None,
) -> str:
    """Build a geocode-friendly jobsite string: street, city, ST.

    Ensures state is always present so Nominatim does not resolve bare streets
    to NYC / other famous names.
    """
    street = (street or "").strip()
    city = (city or "").strip()
    st = normalize_state(state, default="GA")

    # If city already embeds a state/ZIP (legacy "Pooler, GA 31322"), don't
    # duplicate the state abbreviation awkwardly.
    city_has_state = bool(_STATE_RE.search(city)) if city else False

    parts: list[str] = []
    if street:
        parts.append(street)
    if city:
        # Avoid "Pooler, Pooler" if street somehow includes city
        if not street or city.lower() not in street.lower():
            parts.append(city)
    joined = ", ".join(parts) if parts else ""
    if not joined:
        return ""

    if city_has_state or _STATE_RE.search(joined):
        # Still ensure USA bias via bias_address_query later
        return joined
    return f"{joined}, {st}"


def _cache_get(query: str) -> tuple[float, float] | None:
    ensure_geocode_cache_table()
    with db() as conn:
        row = conn.execute(
            "SELECT lat, lon FROM geocode_cache WHERE query = ? COLLATE NOCASE",
            (query,),
        ).fetchone()
        if not row:
            return None
        lat, lon = float(row["lat"]), float(row["lon"])
        # Ignore poisoned cache entries that resolved far from the service area
        # (e.g. "Park Ave" → NYC from before Coastal GA bias existed).
        if not near_base(lat, lon):
            conn.execute("DELETE FROM geocode_cache WHERE query = ? COLLATE NOCASE", (query,))
            return None
        return lat, lon


def _cache_put(query: str, lat: float, lon: float, source: str = "nominatim") -> None:
    ensure_geocode_cache_table()
    with db() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO geocode_cache (query, lat, lon, source, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (query, lat, lon, source, utcnow()),
        )


def _nominatim_search(query: str, *, bounded: bool, limit: int = 5) -> list[dict[str, Any]]:
    _nominatim_throttle()
    params = {
        "q": query,
        "format": "json",
        "limit": limit,
        "countrycodes": "us",
        "viewbox": COASTAL_GA_VIEWBOX,
        "bounded": 1 if bounded else 0,
    }
    data = _http_get_json(f"{NOMINATIM_URL}?{urllib.parse.urlencode(params)}")
    if not data or not isinstance(data, list):
        return []
    return data


def _pick_nearest_local(results: list[dict[str, Any]]) -> tuple[float, float] | None:
    """Among Nominatim hits, prefer the one closest to Pooler within service radius."""
    best: tuple[float, float, float] | None = None  # (dist, lat, lon)
    for row in results:
        try:
            lat = float(row["lat"])
            lon = float(row["lon"])
        except (KeyError, TypeError, ValueError):
            continue
        dist = haversine_miles(BASE_LAT, BASE_LON, lat, lon)
        if dist > SERVICE_RADIUS_MI:
            continue
        if best is None or dist < best[0]:
            best = (dist, lat, lon)
    if best is None:
        return None
    return best[1], best[2]


def geocode(query: str) -> tuple[float, float] | None:
    """Return (lat, lon) for an address string, using SQLite cache + Nominatim.

    Biases toward Coastal Georgia. Picks the Nominatim hit nearest Pooler so
    ambiguous streets (Park Ave, Main St) do not resolve to NYC / Atlanta.
    Far-away cache hits are discarded.
    """
    q = (query or "").strip()
    if not q:
        return None

    cached = _cache_get(q)
    if cached:
        return cached

    biased = bias_address_query(q)
    if biased != q:
        cached = _cache_get(biased)
        if cached:
            _cache_put(q, cached[0], cached[1])
            return cached

    # Try progressively: biased free-text (prefer local), then bounded viewbox,
    # then explicit Pooler / Savannah suffix for bare street names.
    attempts: list[tuple[str, bool]] = [
        (biased, False),
        (biased, True),
    ]
    lower = q.lower()
    if "pooler" not in lower and "savannah" not in lower:
        attempts.append((f"{q}, Pooler, GA, USA", True))
        attempts.append((f"{q}, Savannah, GA, USA", True))

    seen_q: set[str] = set()
    for attempt_q, bounded in attempts:
        key = f"{attempt_q}|{int(bounded)}"
        if key in seen_q:
            continue
        seen_q.add(key)
        picked = _pick_nearest_local(_nominatim_search(attempt_q, bounded=bounded, limit=5))
        if picked:
            lat, lon = picked
            _cache_put(q, lat, lon)
            if biased != q:
                _cache_put(biased, lat, lon)
            if attempt_q not in (q, biased):
                _cache_put(attempt_q, lat, lon)
            return lat, lon

    return None


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

    # Drop suggestions outside the Coastal GA service area (NYC, Ottawa, etc.)
    if lat is None or lon is None or not near_base(lat, lon):
        return None

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
    state_map = {
        "georgia": "GA",
        "south carolina": "SC",
        "florida": "FL",
        "north carolina": "NC",
        "alabama": "AL",
        "tennessee": "TN",
    }
    state_abbr = state_map.get(state.lower(), state)
    if len(state) == 2:
        state_abbr = state.upper()

    postcode = (props.get("postcode") or "").strip()

    if housenumber and street and not street.lower().startswith(housenumber.lower()):
        street_line = f"{housenumber} {street}"
    elif street:
        street_line = street
    elif props.get("name"):
        street_line = str(props["name"]).strip()
    else:
        street_line = ""

    if not street_line and not city:
        return None

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
    formatted = display
    if state_abbr and "USA" not in formatted.upper():
        formatted = f"{formatted}, USA"

    return {
        "display": display,
        "street": street_line,
        "city": city,
        "state": state_abbr,
        "zip": postcode,
        "lat": lat,
        "lon": lon,
        "formatted": formatted.rstrip(", "),
        "distance_mi": round(haversine_miles(BASE_LAT, BASE_LON, lat, lon), 1),
    }


def _nominatim_hit_to_suggestion(row: dict[str, Any]) -> dict[str, Any] | None:
    try:
        lat = float(row["lat"])
        lon = float(row["lon"])
    except (KeyError, TypeError, ValueError):
        return None
    if not near_base(lat, lon):
        return None

    addr = row.get("address") or {}
    housenumber = (addr.get("house_number") or "").strip()
    street = (
        addr.get("road")
        or addr.get("pedestrian")
        or addr.get("residential")
        or addr.get("hamlet")
        or ""
    ).strip()
    city = (
        addr.get("city")
        or addr.get("town")
        or addr.get("village")
        or addr.get("municipality")
        or addr.get("county")
        or ""
    ).strip()
    state_raw = (addr.get("state") or "").strip()
    state_map = {
        "georgia": "GA",
        "south carolina": "SC",
        "florida": "FL",
        "north carolina": "NC",
    }
    state_abbr = state_map.get(state_raw.lower(), state_raw)
    if len(state_raw) == 2:
        state_abbr = state_raw.upper()
    postcode = (addr.get("postcode") or "").strip()

    if housenumber and street:
        street_line = f"{housenumber} {street}"
    elif street:
        street_line = street
    else:
        # Fall back to first comma segment of display_name
        display_name = (row.get("display_name") or "").split(",")[0].strip()
        street_line = display_name

    if not street_line:
        return None

    display_parts = [street_line]
    city_state = ", ".join(p for p in (city, state_abbr) if p)
    if city_state:
        display_parts.append(city_state)
    if postcode:
        display_parts[-1] = f"{display_parts[-1]} {postcode}".strip()
    display = ", ".join(display_parts)
    formatted = f"{display}, USA" if state_abbr else display

    return {
        "display": display,
        "street": street_line,
        "city": city,
        "state": state_abbr,
        "zip": postcode,
        "lat": lat,
        "lon": lon,
        "formatted": formatted,
        "distance_mi": round(haversine_miles(BASE_LAT, BASE_LON, lat, lon), 1),
        "source": "nominatim",
    }


def _suggest_nominatim(query: str, limit: int) -> list[dict[str, Any]]:
    biased = bias_address_query(query)
    _nominatim_throttle()
    params = urllib.parse.urlencode(
        {
            "q": biased,
            "format": "json",
            "addressdetails": 1,
            "limit": max(limit, 5),
            "countrycodes": "us",
            "viewbox": COASTAL_GA_VIEWBOX,
            "bounded": 1,
        }
    )
    data = _http_get_json(f"{NOMINATIM_URL}?{params}", timeout=10.0)
    results: list[dict[str, Any]] = []
    if not data or not isinstance(data, list):
        return results
    for row in data:
        sug = _nominatim_hit_to_suggestion(row)
        if sug:
            results.append(sug)
    return results


_STREET_SUFFIXES = (
    "Avenue", "Ave", "Street", "St", "Drive", "Dr", "Road", "Rd",
    "Boulevard", "Blvd", "Lane", "Ln", "Court", "Ct", "Way", "Parkway", "Pkwy",
)
_QUERY_STOP = {
    "st", "street", "ave", "avenue", "blvd", "boulevard", "rd", "road",
    "dr", "drive", "ln", "lane", "ct", "court", "way", "pkwy", "parkway",
    "ga", "sc", "usa", "us", "the", "of",
}


def _query_tokens(query: str) -> tuple[list[str], list[str]]:
    """Return (word_tokens, number_tokens) from a user query."""
    raw = re.findall(r"[a-z0-9]+", (query or "").lower())
    words = [t for t in raw if not t.isdigit() and t not in _QUERY_STOP and len(t) > 1]
    nums = [t for t in raw if t.isdigit()]
    return words, nums


def _suggestion_matches_query(sug: dict[str, Any], query: str) -> bool:
    """Drop Nominatim/Photon noise that does not share street tokens with q."""
    words, nums = _query_tokens(query)
    street = (sug.get("street") or "").lower()
    display = (sug.get("display") or "").lower()
    hay = f"{street} {display}"
    if words:
        # Every word token must appear in street/display (allows Park → Park Avenue)
        if not all(w in hay for w in words):
            return False
        # Prefer word tokens hitting the street line (not only city)
        if street and not any(w in street for w in words):
            return False
    if nums:
        # If suggestion has a house number, it should match typed number
        leading = street.split()[0] if street else ""
        has_house = bool(sug.get("street") and re.match(r"^\d+", street.strip()))
        if has_house and not any(n == leading or n in street.split()[:2] for n in nums):
            return False
    return True


def _photon_query_variants(query: str) -> list[str]:
    """Bias partial street queries toward Pooler / Savannah / GA."""
    q = (query or "").strip()
    if not q:
        return []
    variants = [q]
    lower = q.lower()
    has_place = bool(re.search(r"\b(ga|georgia|sc|pooler|savannah|pembroke|bluffton)\b", lower))
    if not has_place:
        variants.append(f"{q}, Pooler, GA")
        variants.append(f"{q}, Savannah, GA")
        variants.append(f"{q}, GA")
    # "123 Park" → also try common street suffixes (Nominatim/Photon need them)
    if re.match(r"^\d+\s+[A-Za-z][A-Za-z\s\.]*$", q) and not re.search(
        r"\b(ave|avenue|st|street|rd|road|dr|drive|blvd|boulevard|ln|lane|ct|court|way|pkwy|parkway)\b",
        lower,
    ):
        # Take first street word after house number
        parts = q.split(None, 1)
        if len(parts) == 2:
            num, name = parts
            for suf in ("Avenue", "Street", "Drive", "Road", "Boulevard", "Lane"):
                variants.append(f"{num} {name} {suf}, Pooler, GA")
                variants.append(f"{num} {name} {suf}, Savannah, GA")
    # Dedupe preserving order
    seen: set[str] = set()
    out: list[str] = []
    for v in variants:
        key = v.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(v)
    return out[:10]


def _suggest_photon(query: str, limit: int) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    seen_coord: set[tuple] = set()
    for variant in _photon_query_variants(query):
        # layer=house,street keeps typeahead on addresses/roads
        params = urllib.parse.urlencode(
            [
                ("q", variant),
                ("lat", str(BASE_LAT)),
                ("lon", str(BASE_LON)),
                ("limit", str(max(limit * 2, 8))),
                ("lang", "en"),
                ("layer", "house"),
                ("layer", "street"),
            ]
        )
        data = _http_get_json(f"{PHOTON_URL}?{params}", timeout=6.0)
        if not data or not isinstance(data, dict):
            continue
        for feat in data.get("features") or []:
            sug = _photon_props_to_suggestion(feat)
            if not sug:
                continue
            if not _suggestion_matches_query(sug, query):
                continue
            sug["source"] = "photon"
            key = (round(float(sug["lat"]), 4), round(float(sug["lon"]), 4), (sug.get("street") or "").lower())
            if key in seen_coord:
                continue
            seen_coord.add(key)
            results.append(sug)
        if len(results) >= limit * 2:
            break
    return results


def _rank_suggestion(sug: dict[str, Any], query: str) -> tuple:
    """Prefer first-token street match, house #, Pooler, then nearer hits."""
    words, nums = _query_tokens(query)
    street = (sug.get("street") or "").lower()
    city = (sug.get("city") or "").lower()
    dist = float(sug.get("distance_mi") or 999)
    street_words = [
        t for t in re.findall(r"[a-z0-9]+", street)
        if not t.isdigit() and t not in _QUERY_STOP
    ]
    # "Park" should rank Park Avenue above Durham Park Way
    first_token = 0 if (words and street_words and street_words[0] == words[0]) else 1
    pooler_boost = 0 if "pooler" in city else 1
    if nums and street.startswith(nums[0] + " "):
        house_boost = 0
    elif nums and street_words and not street[:1].isdigit():
        house_boost = 1  # street-level match without house #
    else:
        house_boost = 2 if nums else 0
    return (first_token, house_boost, pooler_boost, dist)


def suggest_addresses(query: str, limit: int = 6) -> list[dict[str, Any]]:
    """Address typeahead biased to Pooler / Coastal GA.

    Photon first (fast typeahead) with Pooler/Savannah query variants, then
    Nominatim as supplement. Irrelevant hits (no shared street tokens) are
    dropped so partials like \"123 Park\" never return Bull Street.
    """
    q = (query or "").strip()
    if len(q) < 3:
        return []
    limit = max(1, min(int(limit or 6), 10))
    cache_key = f"v2|{q.lower()}|{limit}"
    now = time.time()
    cached = _suggest_cache.get(cache_key)
    if cached and cached[0] > now:
        return cached[1]

    # Photon first — no 1 req/s throttle, better for partial streets
    results = _suggest_photon(q, limit=limit)

    # Nominatim on the raw query + first expanded "123 Name Avenue, Pooler, GA"
    # variant so house numbers resolve (Photon often misses them for partials).
    nom_queries = [q]
    for v in _photon_query_variants(q):
        if v != q and "avenue" in v.lower() and "pooler" in v.lower():
            nom_queries.append(v)
            break
    if len(results) < limit:
        for nq in nom_queries:
            for sug in _suggest_nominatim(nq, limit=limit):
                # Match against the *user* query tokens, not the expanded one
                if not _suggestion_matches_query(sug, q):
                    continue
                results.append(sug)
            if len(results) >= limit * 2:
                break

    results.sort(key=lambda s: _rank_suggestion(s, q))
    deduped: list[dict[str, Any]] = []
    seen: set[tuple] = set()
    for s in results:
        key = (
            (s.get("street") or "").lower(),
            (s.get("city") or "").lower(),
            round(float(s["lat"]), 4),
            round(float(s["lon"]), 4),
        )
        if key in seen:
            continue
        seen.add(key)
        # Never put ZIP into city — keep fields clean for the form
        city = (s.get("city") or "").strip()
        # Drop county-as-city noise
        if city.lower().endswith(" county"):
            city = ""
        s = dict(s)
        s["city"] = city
        deduped.append(s)
        if len(deduped) >= limit:
            break

    _suggest_cache[cache_key] = (now + _SUGGEST_TTL, deduped)
    if len(_suggest_cache) > 200:
        expired = [k for k, (exp, _) in _suggest_cache.items() if exp <= now]
        for k in expired:
            _suggest_cache.pop(k, None)
    return deduped



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
    *,
    require_local: bool = False,
) -> tuple[float, float] | None:
    if lat is not None and lon is not None:
        try:
            pt = (float(lat), float(lon))
            if require_local and not near_base(*pt):
                # Far coords (bad Photon hit) — fall through to geocode
                pass
            else:
                return pt
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
    jobsite_state: str | None = None,
) -> dict[str, Any]:
    """
    Miles = Pooler base → supplier pickup → jobsite delivery (one-way total).

    When jobsite_lat/lon are provided and fall inside the Coastal GA service
    radius, those coords are used directly. Far-away coords are ignored and the
    address is re-geocoded with local bias (avoids NYC Park Ave quotes).

    Always composes street + city + state (default GA) for geocode fallback.
    """
    base = (BASE_LAT, BASE_LON)
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
    state = normalize_state(jobsite_state, default="GA")
    jobsite_for_geo = compose_jobsite_query(jobsite, city, state)

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
    # Jobsite coords must be local; otherwise geocode the (biased) address string.
    dest = resolve_point(
        jobsite_for_geo or jobsite,
        j_lat,
        j_lon,
        require_local=True,
    )
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
            "detail": (
                f"Could not locate jobsite near Coastal GA: "
                f"{jobsite_for_geo or jobsite}. "
                f"Include city (e.g. Pooler or Savannah) or pick a suggestion."
            ),
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
            "jobsite_lat": dest[0],
            "jobsite_lon": dest[1],
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
        "jobsite_lat": dest[0],
        "jobsite_lon": dest[1],
    }
