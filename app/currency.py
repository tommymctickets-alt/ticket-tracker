"""Currency conversion (GBP <-> USD via frankfurter.app, no API key needed)."""
import logging
from datetime import date
from functools import lru_cache

import httpx

log = logging.getLogger(__name__)

# Fallback rates if the API is unreachable
_FALLBACKS = {("USD", "GBP"): 0.79, ("GBP", "USD"): 1.27}


@lru_cache(maxsize=16)
def _cached_rate(base: str, target: str, day: str) -> float:
    """Day arg makes the cache rotate daily."""
    if base == target:
        return 1.0
    try:
        r = httpx.get(
            "https://api.frankfurter.app/latest",
            params={"from": base, "to": target},
            timeout=10.0,
        )
        r.raise_for_status()
        return float(r.json()["rates"][target])
    except Exception as e:
        log.warning(f"FX lookup failed ({base}->{target}): {e}; using fallback")
        return _FALLBACKS.get((base, target), 1.0)


def get_rate(base: str, target: str) -> float:
    return _cached_rate(base.upper(), target.upper(), date.today().isoformat())


def convert_to_gbp(amount, currency):
    if amount is None:
        return None
    return amount * get_rate(currency or "GBP", "GBP")


def compute_profit_gbp(
    bought_amount, bought_currency, sold_amount, sold_currency
):
    """Return profit in GBP, or None if either side is missing."""
    if bought_amount is None or sold_amount is None:
        return None
    b = convert_to_gbp(bought_amount, bought_currency or "GBP")
    s = convert_to_gbp(sold_amount, sold_currency or "GBP")
    return round(s - b, 2)
