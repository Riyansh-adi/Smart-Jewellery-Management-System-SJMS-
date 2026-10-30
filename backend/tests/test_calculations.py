from decimal import Decimal
import pytest
from app.services.calculations import money, sale_totals
from app.schemas.requests import ProductCreate

def test_sale_totals_round_at_currency_boundary():
    subtotal, tax, total = sale_totals(
        [{"unit_price": Decimal("19.995"), "quantity": 2}],
        discount=Decimal("1.00"), tax_rate=Decimal("3.0"),
    )
    assert subtotal == Decimal("40.00")
    assert tax == Decimal("1.17")
    assert total == Decimal("40.17")

def test_discount_cannot_exceed_subtotal():
    with pytest.raises(ValueError, match="Discount"):
        sale_totals([{"unit_price": Decimal("10"), "quantity": 1}], discount=11)

def test_weight_schema_rejects_inconsistent_weights():
    with pytest.raises(ValueError, match="net weight plus stone"):
        ProductCreate(sku="X1", name="Ring", gross_weight=3, net_weight=2, stone_weight=2)

def test_money_uses_decimal_half_up():
    assert money("3.145") == Decimal("3.15")
