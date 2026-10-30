"""MongoDB integration tests; use a dedicated replica-set database ending in _test."""
import os
from pathlib import Path
import sys

import pytest
from fastapi.testclient import TestClient
from pymongo import MongoClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.config import settings
from app.core.security import hash_password
from app.db.mongo import ensure_indexes
from app.main import app

TEST_URI = os.getenv("SJMS_TEST_MONGODB_URI", "")
TEST_DB = os.getenv("SJMS_TEST_MONGODB_DATABASE", "sjms_test")

@pytest.fixture(scope="module")
def mongo_db():
    if not TEST_URI:
        pytest.skip("Set SJMS_TEST_MONGODB_URI to a dedicated MongoDB replica set")
    if not TEST_DB.lower().endswith("_test"):
        pytest.fail("Refusing destructive integration setup: MongoDB database name must end in _test")
    client = MongoClient(TEST_URI, serverSelectionTimeoutMS=3000)
    client.admin.command("ping")
    db = client[TEST_DB]
    client.drop_database(TEST_DB)
    ensure_indexes(db)
    previous = (settings.mongodb_uri, settings.mongodb_database, settings.jwt_secret)
    settings.mongodb_uri, settings.mongodb_database, settings.jwt_secret = TEST_URI, TEST_DB, "integration-test-only-secret"
    yield db
    client.drop_database(TEST_DB)
    client.close()
    settings.mongodb_uri, settings.mongodb_database, settings.jwt_secret = previous

@pytest.fixture(scope="module")
def api_client(mongo_db):
    with TestClient(app) as client:
        yield client

@pytest.fixture(scope="module")
def admin_token(api_client, mongo_db):
    mongo_db.users.insert_one({"id": 1, "name": "Test Admin", "email": "admin@sjms.test",
        "password_hash": hash_password("TestPassword123!"), "role": "admin", "active": True})
    response = api_client.post("/api/auth/login", data={"username": "admin@sjms.test", "password": "TestPassword123!"})
    assert response.status_code == 200
    mongo_db.counters.insert_one({"_id": "users", "value": 1})
    return response.json()["access_token"]

def test_authentication_and_role_permissions(api_client, admin_token, mongo_db):
    assert api_client.get("/api/auth/me", headers={"Authorization": f"Bearer {admin_token}"}).status_code == 200
    mongo_db.users.insert_one({"id": 2, "name": "Test Staff", "email": "staff@sjms.test",
        "password_hash": hash_password("StaffPassword123!"), "role": "staff", "active": True})
    token = api_client.post("/api/auth/login", data={"username": "staff@sjms.test", "password": "StaffPassword123!"}).json()["access_token"]
    assert api_client.get("/api/users", headers={"Authorization": f"Bearer {token}"}).status_code == 403

def test_purchase_sale_and_stock_are_transactional(api_client, admin_token, mongo_db):
    headers = {"Authorization": f"Bearer {admin_token}"}
    mongo_db.suppliers.insert_one({"id": 1, "name": "Test Supplier"})
    mongo_db.products.insert_one({"id": 1, "sku": "TEST-RING", "name": "Test Ring", "active": True,
        "stock_qty": 2, "low_stock_at": 1, "selling_price": "150.00", "purchase_cost": "100.00"})
    mongo_db.counters.insert_one({"_id": "suppliers", "value": 1})
    mongo_db.counters.insert_one({"_id": "products", "value": 1})
    purchase = api_client.post("/api/purchases", headers=headers, json={"supplier_id": 1,
        "items": [{"product_id": 1, "quantity": 3, "unit_cost": "110.00"}], "paid_amount": "50.00"})
    assert purchase.status_code == 200, purchase.text
    assert mongo_db.products.find_one({"id": 1})["stock_qty"] == 5
    sale = api_client.post("/api/sales", headers=headers, json={"items": [{"product_id": 1, "quantity": 2}], "tax_rate": "3"})
    assert sale.status_code == 200, sale.text
    assert sale.json()["total"] == "309.00"
    assert mongo_db.products.find_one({"id": 1})["stock_qty"] == 3
    oversell = api_client.post("/api/sales", headers=headers, json={"items": [{"product_id": 1, "quantity": 2}, {"product_id": 1, "quantity": 2}]})
    assert oversell.status_code == 409
    assert mongo_db.products.find_one({"id": 1})["stock_qty"] == 3
