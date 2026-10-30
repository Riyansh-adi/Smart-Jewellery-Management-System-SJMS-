from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from threading import Lock
from time import monotonic

import httpx
from fastapi import HTTPException

from app.core.config import settings

PROVIDER_URL = "https://api.metals.dev/v1/latest"
METALS = (
    ("gold", "Gold"),
    ("silver", "Silver"),
    ("platinum", "Platinum"),
    ("palladium", "Palladium"),
)

_cache_lock = Lock()
_cached_rates: dict | None = None
_cache_time = 0.0


def normalize_latest_response(payload: dict) -> dict:
    if payload.get("status") != "success":
        raise HTTPException(status_code=502, detail="The live metals provider could not return rates.")

    source_rates = payload.get("metals") or {}
    rates = []
    for code, name in METALS:
        try:
            price = Decimal(str(source_rates[code]))
        except (KeyError, InvalidOperation, TypeError, ValueError):
            raise HTTPException(status_code=502, detail=f"The live provider did not return a valid {name.lower()} rate.")
        if not price.is_finite() or price <= 0:
            raise HTTPException(status_code=502, detail=f"The live provider did not return a valid {name.lower()} rate.")
        rates.append({"code": code, "name": name, "price_per_gram": format(price, "f")})

    raw_timestamp = (payload.get("timestamps") or {}).get("metal") or payload.get("timestamp")
    if not raw_timestamp:
        raise HTTPException(status_code=502, detail="The live provider response did not include a rate timestamp.")
    try:
        timestamp = datetime.fromisoformat(str(raw_timestamp).replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
    except ValueError:
        raise HTTPException(status_code=502, detail="The live provider returned an invalid rate timestamp.")

    return {
        "source": "Metals.Dev",
        "currency": payload.get("currency", "INR"),
        "unit": payload.get("unit", "g"),
        "updated_at": timestamp.astimezone(timezone.utc).isoformat(),
        "refresh_seconds": max(60, min(settings.metals_rates_refresh_seconds, 3600)),
        "metals": rates,
    }


def get_live_metal_rates() -> dict:
    global _cached_rates, _cache_time

    if not settings.metals_dev_api_key.strip():
        raise HTTPException(status_code=503, detail="Live rates are not configured. Set METALS_DEV_API_KEY on the backend.")

    refresh_seconds = max(60, min(settings.metals_rates_refresh_seconds, 3600))
    with _cache_lock:
        now = monotonic()
        if _cached_rates and now - _cache_time < refresh_seconds:
            return _cached_rates

        try:
            response = httpx.get(
                PROVIDER_URL,
                params={"api_key": settings.metals_dev_api_key, "currency": "INR", "unit": "g"},
                headers={"Accept": "application/json"},
                timeout=10,
            )
            response.raise_for_status()
            data = normalize_latest_response(response.json())
        except HTTPException:
            raise
        except (httpx.HTTPError, ValueError) as exc:
            raise HTTPException(status_code=502, detail="Could not retrieve live metals rates. Please try again shortly.") from exc

        _cached_rates = data
        _cache_time = monotonic()
        return _cached_rates
