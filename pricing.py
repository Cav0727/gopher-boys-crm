"""Gopher Boys pricing from flyer rates (Pooler supplier → jobsite)."""

BASE_MILES = 10
BASE_PRICE = 110.0
PER_MILE = 3.0
MAX_AUTO_MILES = 30
WAIT_INCLUDED_MIN = 15
WAIT_BLOCK_MIN = 15
WAIT_BLOCK_FEE = 20.0


def calc_delivery_quote(miles: float | None) -> dict:
    """Return quote details. Over 30 miles → custom (no auto price)."""
    if miles is None:
        return {
            "ok": False,
            "custom": False,
            "amount": None,
            "label": "Enter miles",
            "detail": "",
        }
    try:
        m = float(miles)
    except (TypeError, ValueError):
        return {
            "ok": False,
            "custom": False,
            "amount": None,
            "label": "Invalid miles",
            "detail": "",
        }
    if m < 0:
        return {
            "ok": False,
            "custom": False,
            "amount": None,
            "label": "Miles cannot be negative",
            "detail": "",
        }
    if m > MAX_AUTO_MILES:
        return {
            "ok": False,
            "custom": True,
            "amount": None,
            "label": "Custom quote required",
            "detail": f"{m:.1f} mi exceeds 30 mi auto-rate — ask for a quote.",
        }
    if m <= BASE_MILES:
        amount = BASE_PRICE
        detail = f"Up to {BASE_MILES} mi included → ${BASE_PRICE:.0f}"
    else:
        extra = m - BASE_MILES
        amount = BASE_PRICE + extra * PER_MILE
        detail = (
            f"First {BASE_MILES} mi = ${BASE_PRICE:.0f} + "
            f"{extra:.1f} mi × ${PER_MILE:.0f} = ${amount:.2f}"
        )
    return {
        "ok": True,
        "custom": False,
        "amount": round(amount, 2),
        "label": f"${amount:.2f}",
        "detail": detail,
    }


def calc_wait_fee(extra_minutes: float | None) -> dict:
    """First 15 min included; then $20 per started 15 minutes."""
    if extra_minutes is None:
        return {"ok": True, "fee": 0.0, "blocks": 0, "detail": "No extra wait"}
    try:
        mins = float(extra_minutes)
    except (TypeError, ValueError):
        return {"ok": False, "fee": 0.0, "blocks": 0, "detail": "Invalid minutes"}
    if mins <= 0:
        return {"ok": True, "fee": 0.0, "blocks": 0, "detail": "No extra wait"}
    # extra_minutes is minutes BEYOND the included 15
    import math

    blocks = math.ceil(mins / WAIT_BLOCK_MIN)
    fee = blocks * WAIT_BLOCK_FEE
    return {
        "ok": True,
        "fee": round(fee, 2),
        "blocks": blocks,
        "detail": (
            f"{mins:.0f} min beyond included → "
            f"{blocks} × ${WAIT_BLOCK_FEE:.0f} = ${fee:.2f}"
        ),
    }


RATE_TABLE = [
    (10, 110),
    (15, 125),
    (20, 140),
    (25, 155),
    (30, 170),
]
