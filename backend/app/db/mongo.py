from pymongo import MongoClient
from app.core.config import settings

_client = None

def get_client():
    global _client
    if _client is None:
        _client = MongoClient(settings.mongodb_uri, serverSelectionTimeoutMS=3000)
    return _client

def get_db():
    return get_client()[settings.mongodb_database]

def close_client():
    global _client
    if _client is not None:
        _client.close()
        _client = None

def ensure_indexes(db):
    db.users.create_index("email", unique=True)
    db.settings.create_index("key", unique=True)
    db.categories.create_index("name", unique=True)
    db.products.create_index("sku", unique=True)
    db.products.create_index([("name", 1), ("active", 1)])
    db.sales.create_index("invoice_no", unique=True)
    db.sales.create_index("created_at")
    db.purchases.create_index("purchase_no", unique=True)
    db.purchases.create_index("created_at")
    db.repairs.create_index("order_no", unique=True)
    db.repairs.create_index("status")
    db.inventory_movements.create_index([("product_id", 1), ("created_at", -1)])
    db.payments.create_index([("entity_type", 1), ("entity_id", 1)])
    db.metal_rates.create_index([("metal_type", 1), ("recorded_at", -1)])
