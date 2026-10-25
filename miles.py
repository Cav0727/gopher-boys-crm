"""Route miles: Pooler base → supplier → jobsite.

Prefer OSRM public demo for driving distance. Fallback: Nominatim geocode +
haversine × ROAD_FACTOR. Geocodes cached in SQLite. Respects Nominatim
usage policy (User-Agent + ≥1s between requests).
"""

from __future__ import annotations

import json
import math
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
OSRM_URL = "https://router.project-osrm.org/route/v1/driving"
ROAD_FACTOR = 1.30  # approx road miles from great-circle when OSRM unavailable
_last_nominatim_at = 0.0


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


def geocode(query: str) -> tuple[float, float] | None:
    """Return (lat, lon) for an address string, using SQLite cache + Nominatim."""
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

    _nominatim_throttle()
    params = urllib.parse.urlencode(
        {
            "q": q,
            "format": "json",
            "limit": 1,
            "countrycodes": "us",
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
    return lat, lon


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


def calc_route_miles(
    supplier_id: str | None = None,
    supplier_address: str | None = None,
    jobsite_address: str | None = None,
) -> dict[str, Any]:
    """
    Miles = Pooler base → supplier pickup → jobsite delivery (one-way total).

    Returns dict with miles, method, detail, ok, supplier_address, points.
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
    if not pickup_addr:
        return {
            "ok": False,
            "miles": None,
            "method": None,
            "detail": "Pick a supplier or enter a pickup address.",
            "supplier_address": None,
        }
    if not jobsite:
        return {
            "ok": False,
            "miles": None,
            "method": None,
            "detail": "Enter the jobsite / delivery address.",
            "supplier_address": pickup_addr,
        }

    pickup = resolve_point(pickup_addr, pickup_lat, pickup_lon)
    dest = resolve_point(jobsite)
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
            "detail": f"Could not locate jobsite: {jobsite}",
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
