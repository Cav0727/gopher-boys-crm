"""Coastal Georgia / Savannah-area supply yards for the public request form.

Addresses researched from public store locators and HBA directories (2026).
Lat/lon are optional; when missing, Nominatim geocodes and caches them.
"""

from __future__ import annotations

from typing import Any

# Gopher Boys base — Pooler, GA 31322 (pricing origin)
BASE_LABEL = "Gopher Boys base — Pooler, GA 31322"
BASE_ADDRESS = "Pooler, GA 31322"
# Approximate downtown/commercial Pooler near I-95 / US-80 (cached for offline)
BASE_LAT = 32.1155
BASE_LON = -81.2470

# Categories used for <optgroup> labels on /request
CATEGORY_ORDER = [
    "Home Depot",
    "Lowe's",
    "Roofing supply",
    "Lumber yards",
    "Building / construction supply",
]

# Each supplier: id (stable slug), name, category, street, city, state, zip,
# optional lat/lon if known.
SUPPLIERS: list[dict[str, Any]] = [
    # ── Home Depot (metro Savannah / Coastal GA) ──────────────────────────
    {
        "id": "hd-pooler",
        "name": "Home Depot — Pooler #179",
        "category": "Home Depot",
        "street": "190 Pooler Pkwy",
        "city": "Pooler",
        "state": "GA",
        "zip": "31322",
        "lat": 32.1370,
        "lon": -81.2545,
    },
    {
        "id": "hd-east-savannah",
        "name": "Home Depot — East Savannah #170",
        "category": "Home Depot",
        "street": "1901 E Victory Drive",
        "city": "Savannah",
        "state": "GA",
        "zip": "31404",
        "lat": 32.0495,
        "lon": -81.0538,
    },
    {
        "id": "hd-savannah-abercorn",
        "name": "Home Depot — Savannah (Abercorn) #122",
        "category": "Home Depot",
        "street": "11180 Abercorn St",
        "city": "Savannah",
        "state": "GA",
        "zip": "31419",
        "lat": 31.9835,
        "lon": -81.1510,
    },
    {
        "id": "hd-bluffton",
        "name": "Home Depot — Hilton Head / Bluffton #1115",
        "category": "Home Depot",
        "street": "20 Gateway Village Road",
        "city": "Bluffton",
        "state": "SC",
        "zip": "29910",
        "lat": 32.2690,
        "lon": -80.8890,
    },
    # ── Lowe's ────────────────────────────────────────────────────────────
    {
        "id": "lowes-pooler",
        "name": "Lowe's — Pooler",
        "category": "Lowe's",
        "street": "1565 Pooler Parkway",
        "city": "Pooler",
        "state": "GA",
        "zip": "31322",
        "lat": 32.1345,
        "lon": -81.2480,
    },
    {
        "id": "lowes-s-savannah",
        "name": "Lowe's — S. Savannah",
        "category": "Lowe's",
        "street": "11114 Abercorn St",
        "city": "Savannah",
        "state": "GA",
        "zip": "31419",
        "lat": 31.9850,
        "lon": -81.1505,
    },
    {
        "id": "lowes-rincon",
        "name": "Lowe's — Rincon",
        "category": "Lowe's",
        "street": "5150 Highway 21 S",
        "city": "Rincon",
        "state": "GA",
        "zip": "31326",
        "lat": 32.2960,
        "lon": -81.2350,
    },
    {
        "id": "lowes-hinesville",
        "name": "Lowe's — Hinesville",
        "category": "Lowe's",
        "street": "735 West Oglethorpe Highway",
        "city": "Hinesville",
        "state": "GA",
        "zip": "31313",
        "lat": 31.8460,
        "lon": -81.6130,
    },
    # ── Roofing supply ────────────────────────────────────────────────────
    {
        "id": "abc-pooler",
        "name": "ABC Supply — Pooler",
        "category": "Roofing supply",
        "street": "121 Westside Blvd",
        "city": "Pooler",
        "state": "GA",
        "zip": "31322",
        "lat": 32.1205,
        "lon": -81.2550,
    },
    {
        "id": "abc-interiors-savannah",
        "name": "ABC Supply Interiors (L&W) — Savannah",
        "category": "Roofing supply",
        "street": "3 Patton Road, Building 200",
        "city": "Savannah",
        "state": "GA",
        "zip": "31405",
        "lat": 32.0600,
        "lon": -81.1400,
    },
    {
        "id": "srs-savannah",
        "name": "SRS Building Products — Savannah",
        "category": "Roofing supply",
        "street": "40 Westgate Blvd",
        "city": "Savannah",
        "state": "GA",
        "zip": "31405",
        "lat": 32.0655,
        "lon": -81.1450,
    },
    {
        "id": "beacon-qxo-savannah",
        "name": "Beacon / QXO — Savannah #802",
        "category": "Roofing supply",
        "street": "1410 Mills B Lane Blvd",
        "city": "Savannah",
        "state": "GA",
        "zip": "31405",
        "lat": 32.0550,
        "lon": -81.1250,
    },
    {
        "id": "alside-pooler",
        "name": "Alside Supply — Pooler",
        "category": "Roofing supply",
        "street": "176-C Pine Barren Rd",
        "city": "Pooler",
        "state": "GA",
        "zip": "31322",
        "lat": 32.1180,
        "lon": -81.2600,
    },
    {
        "id": "lansing-pooler",
        "name": "Lansing Building Products — Pooler",
        "category": "Roofing supply",
        "street": "5240 Old Louisville Road, Bldg. 42",
        "city": "Pooler",
        "state": "GA",
        "zip": "31322",
        "lat": 32.1050,
        "lon": -81.2700,
    },
    # ── Lumber yards ──────────────────────────────────────────────────────
    {
        "id": "84-lumber-savannah",
        "name": "84 Lumber — Savannah",
        "category": "Lumber yards",
        "street": "4020 Ogeechee Rd",
        "city": "Savannah",
        "state": "GA",
        "zip": "31405",
        "lat": 32.0450,
        "lon": -81.1600,
    },
    {
        "id": "bfs-pooler",
        "name": "Builders FirstSource — Pooler",
        "category": "Lumber yards",
        "street": "6 AJ Garcia Rd",
        "city": "Pooler",
        "state": "GA",
        "zip": "31322",
        "lat": 32.1120,
        "lon": -81.2650,
    },
    {
        "id": "bfs-fleming",
        "name": "Builders FirstSource — Fleming",
        "category": "Lumber yards",
        "street": "456 Pate Rogers Rd",
        "city": "Fleming",
        "state": "GA",
        "zip": "31309",
        "lat": 31.9200,
        "lon": -81.4500,
    },
    {
        "id": "gaster-carter-savannah",
        "name": "Gaster Lumber / Carter Lumber — Savannah",
        "category": "Lumber yards",
        "street": "15010 Abercorn St",
        "city": "Savannah",
        "state": "GA",
        "zip": "31419",
        "lat": 31.9600,
        "lon": -81.1550,
    },
    {
        "id": "guerry-lumber",
        "name": "Guerry Lumber — Savannah",
        "category": "Lumber yards",
        "street": "123 W 44th Street",
        "city": "Savannah",
        "state": "GA",
        "zip": "31405",
        "lat": 32.0500,
        "lon": -81.1050,
    },
    # ── Building / construction supply ────────────────────────────────────
    {
        "id": "capitol-gms-savannah",
        "name": "Capitol Materials Coastal (GMS) — Savannah",
        "category": "Building / construction supply",
        "street": "305 Telfair Rd",
        "city": "Savannah",
        "state": "GA",
        "zip": "31415",
        "lat": 32.0850,
        "lon": -81.1700,
    },
    {
        "id": "fbm-savannah",
        "name": "Foundation Building Materials — Savannah",
        "category": "Building / construction supply",
        "street": "2425 W Gwinnett St",
        "city": "Savannah",
        "state": "GA",
        "zip": "31415",
        "lat": 32.0750,
        "lon": -81.1200,
    },
    {
        "id": "contractors-depot",
        "name": "Contractors Depot — Savannah",
        "category": "Building / construction supply",
        "street": "9 Telfair Place",
        "city": "Savannah",
        "state": "GA",
        "zip": "31415",
        "lat": 32.0820,
        "lon": -81.1680,
    },
    {
        "id": "superior-dist",
        "name": "Superior Distribution — Savannah",
        "category": "Building / construction supply",
        "street": "4102 Meding Street",
        "city": "Savannah",
        "state": "GA",
        "zip": "31405",
        "lat": 32.0480,
        "lon": -81.1300,
    },
    {
        "id": "sandpiper-supply",
        "name": "Sandpiper Supply — Savannah",
        "category": "Building / construction supply",
        "street": "4101 Bull St",
        "city": "Savannah",
        "state": "GA",
        "zip": "31405",
        "lat": 32.0400,
        "lon": -81.1000,
    },
    {
        "id": "four-corners-hardeeville",
        "name": "Four Corners Building Supply — Hardeeville",
        "category": "Building / construction supply",
        "street": "2531 John Smith Road",
        "city": "Hardeeville",
        "state": "SC",
        "zip": "29927",
        "lat": 32.2800,
        "lon": -81.0800,
    },
    {
        "id": "abc-outdoor-pooler",
        "name": "ABC Outdoor Solutions — Pooler",
        "category": "Building / construction supply",
        "street": "5240 Old Louisville Rd Bldg 44",
        "city": "Pooler",
        "state": "GA",
        "zip": "31322",
        "lat": 32.1045,
        "lon": -81.2695,
    },
    {
        "id": "ferguson-savannah",
        "name": "Ferguson Enterprises — Savannah",
        "category": "Building / construction supply",
        "street": "1275 Chatham Parkway",
        "city": "Savannah",
        "state": "GA",
        "zip": "31405",
        "lat": 32.0555,
        "lon": -81.1550,
    },
]


def full_address(s: dict[str, Any]) -> str:
    street = (s.get("street") or "").strip()
    city = (s.get("city") or "").strip()
    state = (s.get("state") or "").strip()
    zipc = (s.get("zip") or "").strip()
    parts = [p for p in (street, f"{city}, {state} {zipc}".strip()) if p]
    return ", ".join(parts)


def supplier_label(s: dict[str, Any]) -> str:
    return f"{s['name']} — {full_address(s)}"


def get_supplier(supplier_id: str | None) -> dict[str, Any] | None:
    if not supplier_id or supplier_id == "other":
        return None
    for s in SUPPLIERS:
        if s["id"] == supplier_id:
            return s
    return None


def suppliers_by_category() -> list[tuple[str, list[dict[str, Any]]]]:
    grouped: dict[str, list[dict[str, Any]]] = {c: [] for c in CATEGORY_ORDER}
    for s in SUPPLIERS:
        cat = s.get("category") or "Building / construction supply"
        grouped.setdefault(cat, []).append(s)
    out = []
    for cat in CATEGORY_ORDER:
        if grouped.get(cat):
            out.append((cat, grouped[cat]))
    for cat, items in grouped.items():
        if cat not in CATEGORY_ORDER and items:
            out.append((cat, items))
    return out


def supplier_payload_for_js() -> list[dict[str, Any]]:
    """Minimal JSON for the public form (addresses + coords when known)."""
    rows = []
    for s in SUPPLIERS:
        rows.append(
            {
                "id": s["id"],
                "name": s["name"],
                "category": s["category"],
                "address": full_address(s),
                "lat": s.get("lat"),
                "lon": s.get("lon"),
            }
        )
    return rows
