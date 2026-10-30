"""Explicit demo-data command: python -m app.seed"""
from datetime import datetime, timezone
from decimal import Decimal
from bson.decimal128 import Decimal128
from pymongo import ReturnDocument
from app.db.mongo import get_db, ensure_indexes
from app.core.security import hash_password

def next_id(db, collection):
    row = db.counters.find_one_and_update({"_id": collection}, {"$inc": {"value": 1}}, upsert=True, return_document=ReturnDocument.AFTER)
    return row["value"]

def main():
    db = get_db()
    db.command("ping")
    ensure_indexes(db)
    if db.users.find_one({"email": "admin@sjms.demo"}):
        print("Demo data already exists; no changes made.")
        return
    now = datetime.now(timezone.utc)
    user_id, supplier_id = next_id(db, "users"), next_id(db, "suppliers")
    category_id, customer_id = next_id(db, "categories"), next_id(db, "customers")
    db.users.insert_one({"id": user_id, "name": "Demo Administrator", "email": "admin@sjms.demo",
        "password_hash": hash_password("ChangeMe123!"), "role": "admin", "active": True, "created_at": now})
    db.suppliers.insert_one({"id": supplier_id, "name": "Aurum Fine Metals", "phone": "+91 98765 43210",
        "email": "trade@aurum.example", "address": "", "tax_id": "DEMO-GST-001", "created_at": now})
    db.categories.insert_one({"id": category_id, "name": "Gold Jewellery", "metal_type": "Gold"})
    db.customers.insert_one({"id": customer_id, "name": "Ananya Mehta", "phone": "+91 98765 12345",
        "email": "ananya@example.test", "address": "", "loyalty_points": 240, "created_at": now})
    products = [
        ("AU-2201", "Mira Gold Necklace", "Gold", "22K", "28.500", "26.800", "1.700", "154000", "179500", 4, "8901002201"),
        ("AU-2202", "Luna Temple Earrings", "Gold", "22K", "9.200", "8.600", "0.600", "48700", "56800", 1, "8901002202"),
        ("AU-1808", "Solitaire Pendant", "Diamond", "18K", "4.800", "3.200", "1.600", "37200", "44900", 6, "8901001808"),
    ]
    for sku, name, metal, purity, gross, net, stone, cost, price, quantity, barcode in products:
        product_id = next_id(db, "products")
        db.products.insert_one({"id": product_id, "sku": sku, "name": name, "category_id": category_id,
            "metal_type": metal, "purity": purity, "gross_weight": Decimal128(Decimal(gross)), "net_weight": Decimal128(Decimal(net)),
            "stone_weight": Decimal128(Decimal(stone)), "wastage_percent": Decimal128(Decimal("0")), "making_charge": Decimal128(Decimal("0")),
            "supplier_id": supplier_id, "purchase_cost": Decimal128(Decimal(cost)), "selling_price": Decimal128(Decimal(price)),
            "stock_qty": quantity, "low_stock_at": 2, "barcode": barcode, "active": True, "created_at": now})
        db.inventory_movements.insert_one({"id": next_id(db, "inventory_movements"), "product_id": product_id,
            "movement_type": "opening", "quantity": quantity, "reference": "explicit demo seed", "created_at": now})
    db.settings.insert_many([{"key": "business_name", "value": "Aurelia Jewellers"}, {"key": "tax_rate", "value": "3.0"}, {"key": "currency", "value": "INR"}])
    print("Demo admin: admin@sjms.demo / ChangeMe123! — change this password after first login.")

if __name__ == "__main__": main()
