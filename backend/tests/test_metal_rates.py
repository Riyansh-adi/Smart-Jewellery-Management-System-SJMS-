import pytest
from fastapi import HTTPException
from datetime import datetime, timezone
from copy import deepcopy

from app.core.config import settings
from app.services import metal_rates
from app.services.metal_rates import normalize_latest_response


class FakeCollection:
    def __init__(self):
        self.docs = {}

    def update_one(self, query, update, upsert=False):
        key = query["_id"]
        doc = self.docs.get(key)
        if doc is None and upsert:
            doc = {"_id": key, **deepcopy(update.get("$setOnInsert", {}))}
            self.docs[key] = doc
        return None

    def find_one(self, query):
        doc = self.docs.get(query["_id"])
        return deepcopy(doc) if doc else None

    def find_one_and_update(self, query, update, return_document=None):
        doc = self.docs.get(query["_id"])
        if not doc or doc.get("requests_used") != query.get("requests_used"):
            return None
        if doc.get("next_request_at") > query["next_request_at"]["$lte"]:
            return None
        for key, amount in update.get("$inc", {}).items():
            doc[key] = doc.get(key, 0) + amount
        doc.update(deepcopy(update.get("$set", {})))
        return deepcopy(doc)

    def replace_one(self, query, replacement, upsert=False):
        self.docs[query["_id"]] = deepcopy(replacement)


class FakeDatabase:
    def __init__(self):
        self.collections = {}

    def __getattr__(self, name):
        return self.collections.setdefault(name, FakeCollection())


class FakeResponse:
    def raise_for_status(self):
        return None

    def json(self):
        return {
            "status": "success", "currency": "INR", "unit": "g",
            "metals": {"gold": 7200, "silver": 88, "platinum": 3100, "palladium": 2900},
            "timestamps": {"metal": "2026-10-30T07:00:00Z"},
        }


def test_normalizes_live_rates_to_inr_per_gram_with_timestamp():
    result = normalize_latest_response({
        "status": "success",
        "currency": "INR",
        "unit": "g",
        "metals": {"gold": 7200.125, "silver": 88.5, "platinum": 3100, "palladium": 2900},
        "timestamps": {"metal": "2026-10-30T07:00:00Z"},
    })

    assert result["currency"] == "INR"
    assert result["unit"] == "g"
    assert result["updated_at"] == "2026-10-30T07:00:00+00:00"
    assert result["metals"] == [
        {"code": "gold", "name": "Gold", "price_per_gram": "7200.125"},
        {"code": "silver", "name": "Silver", "price_per_gram": "88.5"},
        {"code": "platinum", "name": "Platinum", "price_per_gram": "3100"},
        {"code": "palladium", "name": "Palladium", "price_per_gram": "2900"},
    ]


@pytest.mark.parametrize("payload", [
    {"status": "failure"},
    {"status": "success", "metals": {"gold": 0}, "timestamps": {"metal": "2026-10-30T07:00:00Z"}},
    {"status": "success", "metals": {"gold": 1, "silver": 1, "platinum": 1, "palladium": 1}},
])
def test_rejects_incomplete_or_invalid_provider_data(payload):
    with pytest.raises(HTTPException) as exc:
        normalize_latest_response(payload)
    assert exc.value.status_code == 502


def test_reuses_saved_quote_until_monthly_request_slot(monkeypatch):
    db = FakeDatabase()
    calls = []
    monkeypatch.setattr(settings, "metals_dev_api_key", "test-key")
    monkeypatch.setattr(metal_rates.httpx, "get", lambda *args, **kwargs: calls.append(kwargs) or FakeResponse())

    first = metal_rates.get_live_metal_rates(db)
    second = metal_rates.get_live_metal_rates(db)

    month = datetime.now(timezone.utc).strftime("%Y-%m")
    assert len(calls) == 1
    assert first["monthly_requests_used"] == second["monthly_requests_used"] == 1
    assert second["monthly_requests_limit"] == 100
    assert db.metal_rate_api_usage.find_one({"_id": month})["requests_used"] == 1


def test_monthly_cap_serves_saved_quote_without_calling_provider(monkeypatch):
    db = FakeDatabase()
    now = datetime.now(timezone.utc)
    month = now.strftime("%Y-%m")
    db.metal_rate_api_usage.update_one(
        {"_id": month},
        {"$setOnInsert": {"requests_used": 0, "next_request_at": now}},
        upsert=True,
    )
    db.metal_rate_api_usage.docs[month]["requests_used"] = 100
    db.metal_rate_api_usage.docs[month]["next_request_at"] = metal_rates.next_month_start(now)
    db.metal_rate_snapshots.replace_one({"_id": "latest"}, {"_id": "latest", "data": normalize_latest_response(FakeResponse().json())}, upsert=True)
    monkeypatch.setattr(settings, "metals_dev_api_key", "test-key")
    monkeypatch.setattr(metal_rates.httpx, "get", lambda *args, **kwargs: pytest.fail("provider request exceeded the monthly cap"))

    result = metal_rates.get_live_metal_rates(db)

    assert result["monthly_requests_used"] == result["monthly_requests_limit"] == 100
    assert result["quota_exhausted"] is True
