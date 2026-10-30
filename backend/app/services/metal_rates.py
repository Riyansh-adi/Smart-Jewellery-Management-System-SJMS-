from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from math import ceil

import httpx
from fastapi import HTTPException
from pymongo import ReturnDocument
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

from app.core.config import settings

PROVIDER_URL = "https://api.metals.dev/v1/latest"
MONTHLY_REQUEST_LIMIT = 100
METALS = (
    ("gold", "Gold"),
    ("silver", "Silver"),
    ("platinum", "Platinum"),
    ("palladium", "Palladium"),
)


def as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


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
        "metals": rates,
    }


def next_month_start(now: datetime) -> datetime:
    year, month = (now.year + 1, 1) if now.month == 12 else (now.year, now.month + 1)
    return datetime(year, month, 1, tzinfo=timezone.utc)


def paced_next_request(now: datetime, requests_used_after_call: int) -> datetime:
    month_end = next_month_start(now)
    remaining = MONTHLY_REQUEST_LIMIT - requests_used_after_call
    if remaining <= 0:
        return month_end
    seconds_left = max(0, (month_end - now).total_seconds())
    return now + timedelta(seconds=ceil(seconds_left / remaining))


def _usage_for_month(db: Database, now: datetime) -> dict:
    month_key = now.strftime("%Y-%m")
    collection = db.metal_rate_api_usage
    try:
        collection.update_one(
            {"_id": month_key},
            {"$setOnInsert": {"requests_used": 0, "next_request_at": now}},
            upsert=True,
        )
    except DuplicateKeyError:
        # Another app worker may have initialized this month's quota concurrently.
        pass
    return collection.find_one({"_id": month_key})


def _with_usage(data: dict, usage: dict, now: datetime) -> dict:
    result = dict(data)
    result["monthly_requests_used"] = int(usage.get("requests_used", 0))
    result["monthly_requests_limit"] = MONTHLY_REQUEST_LIMIT
    next_refresh = as_utc(usage.get("next_request_at") or now)
    result["next_refresh_at"] = next_refresh.isoformat()
    result["quota_exhausted"] = result["monthly_requests_used"] >= MONTHLY_REQUEST_LIMIT
    return result


def get_live_metal_rates(db: Database) -> dict:
    if not settings.metals_dev_api_key.strip():
        raise HTTPException(status_code=503, detail="Live rates are not configured. Set METALS_DEV_API_KEY on the backend.")

    now = datetime.now(timezone.utc)
    month_key = now.strftime("%Y-%m")
    usage = _usage_for_month(db, now)
    snapshot_collection = db.metal_rate_snapshots
    snapshot = snapshot_collection.find_one({"_id": "latest"})

    used = int(usage.get("requests_used", 0))
    due_at = as_utc(usage.get("next_request_at") or now)
    if used >= MONTHLY_REQUEST_LIMIT or due_at > now:
        if not snapshot:
            detail = "The monthly live-rate request is scheduled for later, but no rate snapshot has been saved yet."
            raise HTTPException(status_code=503, detail=detail)
        return _with_usage(snapshot["data"], usage, now)

    next_at = paced_next_request(now, used + 1)
    claimed = db.metal_rate_api_usage.find_one_and_update(
        {"_id": month_key, "requests_used": used, "next_request_at": {"$lte": now}},
        {"$inc": {"requests_used": 1}, "$set": {"next_request_at": next_at, "last_request_at": now}},
        return_document=ReturnDocument.AFTER,
    )
    if not claimed:
        usage = _usage_for_month(db, now)
        if not snapshot:
            raise HTTPException(status_code=503, detail="A live rate update is already in progress. Please retry shortly.")
        return _with_usage(snapshot["data"], usage, now)

    try:
        response = httpx.get(
            PROVIDER_URL,
            params={"api_key": settings.metals_dev_api_key, "currency": "INR", "unit": "g"},
            headers={"Accept": "application/json"},
            timeout=10,
        )
        response.raise_for_status()
        data = normalize_latest_response(response.json())
    except HTTPException as exc:
        if snapshot:
            stale = dict(snapshot["data"])
            stale["refresh_warning"] = "The provider refresh failed; showing the last saved quote."
            return _with_usage(stale, claimed, now)
        raise exc
    except (httpx.HTTPError, ValueError) as exc:
        if snapshot:
            stale = dict(snapshot["data"])
            stale["refresh_warning"] = "The provider refresh failed; showing the last saved quote."
            return _with_usage(stale, claimed, now)
        raise HTTPException(status_code=502, detail="Could not retrieve live metals rates. Please try again shortly.") from exc

    snapshot_collection.replace_one({"_id": "latest"}, {"_id": "latest", "data": data}, upsert=True)
    return _with_usage(data, claimed, now)
