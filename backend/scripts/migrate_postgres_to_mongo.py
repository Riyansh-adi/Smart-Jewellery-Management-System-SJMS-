"""One-time copy from the prior SJMS PostgreSQL schema into an empty MongoDB database.

Requires LEGACY_POSTGRES_URL and MONGODB_URI/MONGODB_DATABASE in the environment.
Run from backend: python scripts/migrate_postgres_to_mongo.py
"""
import os
from datetime import date, datetime, time, timezone
from decimal import Decimal
from pathlib import Path
import sys

from bson.decimal128 import Decimal128
from pymongo import MongoClient
from sqlalchemy import create_engine, inspect, text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.config import settings
from app.models.documents import COLLECTIONS
from app.db.mongo import ensure_indexes

TABLES = ["users", "customers", "suppliers", "categories", "products", "sales", "purchases", "repairs", "payments", "inventory_movements", "settings", "metal_rates"]

def convert(value):
    if isinstance(value, Decimal): return Decimal128(value)
    if isinstance(value, date) and not isinstance(value, datetime): return datetime.combine(value, time.min, tzinfo=timezone.utc)
    if isinstance(value, datetime) and value.tzinfo is None: return value.replace(tzinfo=timezone.utc)
    if isinstance(value, dict): return {key: convert(item) for key, item in value.items()}
    if isinstance(value, list): return [convert(item) for item in value]
    return value

def main():
    legacy_url = (settings.legacy_postgres_url or os.getenv("LEGACY_POSTGRES_URL", "")).strip()
    if not legacy_url: raise SystemExit("Set LEGACY_POSTGRES_URL to the PostgreSQL URL before migrating.")
    source = create_engine(legacy_url, pool_pre_ping=True)
    mongo = MongoClient(settings.mongodb_uri, serverSelectionTimeoutMS=5000)
    target = mongo[settings.mongodb_database]
    mongo.admin.command("ping")
    ensure_indexes(target)
    occupied = [name for name in COLLECTIONS if name != "counters" and target[name].count_documents({})]
    if occupied:
        raise SystemExit("Refusing to merge into non-empty collections: " + ", ".join(occupied))
    inspector = inspect(source)
    present = set(inspector.get_table_names())
    missing = [name for name in TABLES if name not in present]
    if missing: raise SystemExit("PostgreSQL schema is missing expected tables: " + ", ".join(missing))
    legacy = {}
    with source.connect() as connection:
        for table in TABLES:
            legacy[table] = [dict(row) for row in connection.execute(text(f'SELECT * FROM "{table}"')).mappings()]
        sale_items = [dict(row) for row in connection.execute(text('SELECT * FROM "sale_items"')).mappings()]
        purchase_items = [dict(row) for row in connection.execute(text('SELECT * FROM "purchase_items"')).mappings()]
    products = {x["id"]: x for x in legacy["products"]}
    sales_by_id = {x["id"]: x for x in legacy["sales"]}
    purchases_by_id = {x["id"]: x for x in legacy["purchases"]}
    for row in sale_items:
        product = products.get(row["product_id"], {})
        sales_by_id[row["sale_id"]].setdefault("items", []).append({"product_id": row["product_id"], "name": product.get("name", ""),
            "sku": product.get("sku", ""), "quantity": row["quantity"], "unit_price": row["unit_price"], "line_total": row["line_total"]})
    for row in purchase_items:
        product = products.get(row["product_id"], {})
        purchases_by_id[row["purchase_id"]].setdefault("items", []).append({"product_id": row["product_id"], "name": product.get("name", ""),
            "quantity": row["quantity"], "unit_cost": row["unit_cost"]})
    legacy["sales"] = list(sales_by_id.values())
    legacy["purchases"] = list(purchases_by_id.values())
    counts = {}
    for collection in TABLES:
        documents = [{key: convert(value) for key, value in row.items()} for row in legacy[collection]]
        if documents: target[collection].insert_many(documents, ordered=True)
        counts[collection] = len(documents)
    for collection in TABLES:
        if collection == "settings": continue
        maximum = max((int(row.get("id", 0)) for row in legacy[collection]), default=0)
        if maximum: target.counters.update_one({"_id": collection}, {"$set": {"value": maximum}}, upsert=True)
    print("Copied PostgreSQL records to MongoDB:")
    for collection, count in counts.items(): print(f"  {collection}: {count}")
    print(f"  sales: embedded {len(sale_items)} sale items; purchases: embedded {len(purchase_items)} purchase items")
    source.dispose()
    mongo.close()

if __name__ == "__main__": main()
