import pytest
from fastapi import HTTPException

from app.services.metal_rates import normalize_latest_response


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
