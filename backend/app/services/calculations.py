from decimal import Decimal, ROUND_HALF_UP

CENT = Decimal("0.01")
def money(value) -> Decimal:
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)

def sale_totals(lines, discount=0, tax_rate=0):
    subtotal = money(sum((money(row["unit_price"]) * int(row["quantity"]) for row in lines), Decimal("0")))
    discount = money(discount)
    if discount < 0 or discount > subtotal:
        raise ValueError("Discount must be between zero and subtotal")
    taxable = subtotal - discount
    tax = money(taxable * Decimal(str(tax_rate)) / Decimal("100"))
    return subtotal, tax, money(taxable + tax)

