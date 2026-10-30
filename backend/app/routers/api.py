import csv
import io
import re
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any

from bson.decimal128 import Decimal128
from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.security import OAuth2PasswordRequestForm
from pymongo import ReturnDocument
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

from app.core.config import settings
from app.core.security import current_user, require_roles, verify_password, hash_password, create_access_token
from app.db.mongo import get_db, get_client
from app.schemas.requests import CustomerCreate, SupplierCreate, ProductCreate, SaleCreate, PurchaseCreate, RepairCreate, UserCreate
from app.services.calculations import money, sale_totals
from app.services.metal_rates import get_live_metal_rates

router = APIRouter()
ADMIN = require_roles("admin")

def decimal_value(value: Any) -> Decimal:
    if isinstance(value, Decimal128): return value.to_decimal()
    return Decimal(str(value or 0))

def store_value(value: Any) -> Any:
    if isinstance(value, Decimal): return Decimal128(value)
    if isinstance(value, date) and not isinstance(value, datetime):
        return datetime.combine(value, time.min, tzinfo=timezone.utc)
    if isinstance(value, dict): return {k: store_value(v) for k, v in value.items()}
    if isinstance(value, list): return [store_value(v) for v in value]
    return value

def serialize(value: Any) -> Any:
    if isinstance(value, Decimal128): return str(value.to_decimal())
    if isinstance(value, datetime): return value.isoformat()
    if isinstance(value, date): return value.isoformat()
    if isinstance(value, dict): return {k: serialize(v) for k, v in value.items() if k != "_id"}
    if isinstance(value, list): return [serialize(v) for v in value]
    return value

def public_user(user: dict) -> dict:
    return {k: serialize(v) for k, v in user.items() if k not in {"password_hash", "_id"}}

def next_id(db: Database, collection: str, session=None) -> int:
    counter = db.counters.find_one_and_update(
        {"_id": collection}, {"$inc": {"value": 1}}, upsert=True,
        return_document=ReturnDocument.AFTER, session=session,
    )
    return counter["value"]

def list_rows(db: Database, collection: str, search: str = "") -> list[dict]:
    query = {"name": {"$regex": re.escape(search), "$options": "i"}} if search else {}
    return [serialize(row) for row in db[collection].find(query).sort("id", -1)]

def date_query(start: date | None, end: date | None) -> dict:
    query = {}
    if start: query["$gte"] = datetime.combine(start, time.min, tzinfo=timezone.utc)
    if end: query["$lt"] = datetime.combine(end + timedelta(days=1), time.min, tzinfo=timezone.utc)
    return {"created_at": query} if query else {}

def create_payment(db: Database, entity_type: str, entity_id: int, amount: Decimal, method: str, reference: str, session):
    if amount <= 0: return
    db.payments.insert_one({"id": next_id(db, "payments", session), "entity_type": entity_type,
        "entity_id": entity_id, "amount": Decimal128(amount), "method": method,
        "reference": reference, "created_at": datetime.now(timezone.utc)}, session=session)

@router.post("/auth/login")
def login(form: OAuth2PasswordRequestForm = Depends(), db: Database = Depends(get_db)):
    user = db.users.find_one({"email": form.username.lower()})
    if not user or not verify_password(form.password, user["password_hash"]) or not user.get("active", True):
        raise HTTPException(401, "Email or password is incorrect")
    return {"access_token": create_access_token(str(user["id"])), "token_type": "bearer", "user": public_user(user)}

@router.get("/auth/me")
def me(user: dict = Depends(current_user)):
    return public_user(user)

@router.get("/users")
def users(db: Database = Depends(get_db), _user=Depends(ADMIN)):
    return [public_user(x) for x in db.users.find().sort("name", 1)]

@router.post("/users")
def create_user(body: UserCreate, db: Database = Depends(get_db), _user=Depends(ADMIN)):
    doc = body.model_dump()
    doc.update(id=next_id(db, "users"), email=doc["email"].lower(), password_hash=hash_password(doc.pop("password")), active=True,
               created_at=datetime.now(timezone.utc))
    try: db.users.insert_one(doc)
    except DuplicateKeyError: raise HTTPException(409, "Email already exists")
    return public_user(doc)

@router.get("/dashboard")
def dashboard(db: Database = Depends(get_db), _user=Depends(current_user)):
    now = datetime.now(timezone.utc)
    start_today = datetime.combine(now.date(), time.min, tzinfo=timezone.utc)
    start_month = start_today.replace(day=1)
    sales_today = sum((decimal_value(x["total"]) for x in db.sales.find({"created_at": {"$gte": start_today}})), Decimal("0"))
    revenue_month = sum((decimal_value(x["total"]) for x in db.sales.find({"created_at": {"$gte": start_month}})), Decimal("0"))
    inventory_value = Decimal("0")
    products = list(db.products.find({"active": True}).sort("name", 1))
    low = []
    for product in products:
        inventory_value += decimal_value(product.get("purchase_cost")) * int(product.get("stock_qty", 0))
        if product.get("stock_qty", 0) <= product.get("low_stock_at", 2): low.append(serialize(product))
    repairs = db.repairs.count_documents({"status": {"$nin": ["Ready", "Delivered", "Cancelled"]}})
    recent = list(db.sales.find().sort("created_at", -1).limit(6))
    customer_ids = [x.get("customer_id") for x in recent if x.get("customer_id")]
    customer_names = {x["id"]: x["name"] for x in db.customers.find({"id": {"$in": customer_ids}})} if customer_ids else {}
    weekly = []
    for offset in range(6, -1, -1):
        day = now.date() - timedelta(days=offset)
        beginning = datetime.combine(day, time.min, tzinfo=timezone.utc)
        ending = beginning + timedelta(days=1)
        amount = sum((decimal_value(x["total"]) for x in db.sales.find({"created_at": {"$gte": beginning, "$lt": ending}})), Decimal("0"))
        weekly.append({"date": day.isoformat(), "revenue": str(amount)})
    return {"sales_today": str(sales_today), "revenue_month": str(revenue_month), "inventory_value": str(inventory_value),
            "low_stock_count": len(low), "pending_repairs": repairs, "low_stock": low[:6],
            "recent_sales": [{"id": x["id"], "invoice_no": x["invoice_no"], "customer": customer_names.get(x.get("customer_id"), "Walk-in"),
                              "total": str(decimal_value(x["total"])), "paid_amount": str(decimal_value(x.get("paid_amount"))),
                              "created_at": x["created_at"].isoformat()} for x in recent], "weekly_sales": weekly}

@router.get("/customers")
def customers(search: str = "", db: Database = Depends(get_db), _user=Depends(current_user)):
    return list_rows(db, "customers", search)

@router.post("/customers")
def create_customer(body: CustomerCreate, db: Database = Depends(get_db), _user=Depends(current_user)):
    doc = store_value(body.model_dump())
    doc.update(id=next_id(db, "customers"), loyalty_points=0, created_at=datetime.now(timezone.utc))
    db.customers.insert_one(doc)
    return serialize(doc)

@router.get("/customers/{customer_id}/history")
def customer_history(customer_id: int, db: Database = Depends(get_db), _user=Depends(current_user)):
    if not db.customers.find_one({"id": customer_id}): raise HTTPException(404, "Customer not found")
    sales = list(db.sales.find({"customer_id": customer_id}).sort("created_at", -1))
    repairs = list(db.repairs.find({"customer_id": customer_id}).sort("created_at", -1))
    sale_ids = [x["id"] for x in sales]
    payments = list(db.payments.find({"entity_type": "sale", "entity_id": {"$in": sale_ids}})) if sale_ids else []
    return {"sales": [serialize(x) for x in sales], "repairs": [serialize(x) for x in repairs], "payments": [serialize(x) for x in payments]}

@router.get("/suppliers")
def suppliers(search: str = "", db: Database = Depends(get_db), _user=Depends(current_user)):
    return list_rows(db, "suppliers", search)

@router.post("/suppliers")
def create_supplier(body: SupplierCreate, db: Database = Depends(get_db), _user=Depends(current_user)):
    doc = store_value(body.model_dump())
    doc.update(id=next_id(db, "suppliers"), created_at=datetime.now(timezone.utc))
    db.suppliers.insert_one(doc)
    return serialize(doc)

@router.get("/categories")
def categories(db: Database = Depends(get_db), _user=Depends(current_user)):
    return list_rows(db, "categories")

@router.post("/categories")
def create_category(body: dict, db: Database = Depends(get_db), _user=Depends(ADMIN)):
    doc = {"id": next_id(db, "categories"), "name": body["name"], "metal_type": body.get("metal_type", "Gold")}
    db.categories.insert_one(doc)
    return serialize(doc)

@router.get("/products")
def products(search: str = "", db: Database = Depends(get_db), _user=Depends(current_user)):
    query = {"active": True}
    if search: query["$or"] = [{"name": {"$regex": re.escape(search), "$options": "i"}}, {"sku": {"$regex": re.escape(search), "$options": "i"}}, {"barcode": {"$regex": re.escape(search), "$options": "i"}}]
    return [serialize(x) for x in db.products.find(query).sort("name", 1)]

@router.post("/products")
def create_product(body: ProductCreate, db: Database = Depends(get_db), _user=Depends(current_user)):
    doc = store_value(body.model_dump())
    doc.update(id=next_id(db, "products"), active=True, created_at=datetime.now(timezone.utc))
    try: db.products.insert_one(doc)
    except DuplicateKeyError: raise HTTPException(409, "SKU already exists")
    if doc.get("stock_qty"):
        db.inventory_movements.insert_one({"id": next_id(db, "inventory_movements"), "product_id": doc["id"],
            "movement_type": "opening", "quantity": doc["stock_qty"], "reference": "product creation", "created_at": datetime.now(timezone.utc)})
    return serialize(doc)

@router.patch("/products/{product_id}")
def update_product(product_id: int, body: dict, db: Database = Depends(get_db), _user=Depends(current_user)):
    row = db.products.find_one({"id": product_id})
    if not row: raise HTTPException(404, "Product not found")
    allowed = {"name", "sku", "category_id", "metal_type", "purity", "gross_weight", "net_weight", "stone_weight", "wastage_percent", "making_charge", "supplier_id", "purchase_cost", "selling_price", "low_stock_at", "barcode", "active"}
    update = store_value({k: v for k, v in body.items() if k in allowed})
    candidate = {**row, **update}
    if decimal_value(candidate.get("gross_weight")) < decimal_value(candidate.get("net_weight")) + decimal_value(candidate.get("stone_weight")):
        raise HTTPException(422, "Gross weight must be at least net weight plus stone weight")
    try: db.products.update_one({"id": product_id}, {"$set": update})
    except DuplicateKeyError: raise HTTPException(409, "SKU already exists")
    return serialize(db.products.find_one({"id": product_id}))

@router.post("/sales")
def create_sale(body: SaleCreate, db: Database = Depends(get_db), user: dict = Depends(current_user)):
    payload = body.model_dump(exclude_none=True)
    lines = payload.get("items", [])
    if not lines: raise HTTPException(422, "Add at least one item")
    if payload.get("customer_id") and not db.customers.find_one({"id": int(payload["customer_id"])}): raise HTTPException(404, "Customer not found")
    requested = {}
    for line in lines: requested[int(line["product_id"])] = requested.get(int(line["product_id"]), 0) + int(line["quantity"])
    try:
        with get_client().start_session() as session:
            with session.start_transaction():
                selected = {pid: db.products.find_one({"id": pid, "active": True}, session=session) for pid in requested}
                for pid, qty in requested.items():
                    product = selected[pid]
                    if not product: raise HTTPException(404, f"Product {pid} was not found")
                    result = db.products.update_one({"id": pid, "stock_qty": {"$gte": qty}}, {"$inc": {"stock_qty": -qty}}, session=session)
                    if result.modified_count != 1: raise HTTPException(409, f"Insufficient stock for {product['name']}")
                normalized = []
                for line in lines:
                    product = selected[int(line["product_id"])]
                    normalized.append({"product": product, "quantity": int(line["quantity"]),
                                       "unit_price": money(line.get("unit_price", decimal_value(product["selling_price"])))})
                try: subtotal, tax, total = sale_totals(normalized, payload.get("discount", 0), payload.get("tax_rate", 0))
                except ValueError as exc: raise HTTPException(422, str(exc))
                paid = money(payload.get("paid_amount", total))
                if paid < 0 or paid > total: raise HTTPException(422, "Paid amount must be between zero and invoice total")
                sale_id = next_id(db, "sales", session)
                invoice = f"INV-{datetime.now(timezone.utc):%Y%m%d-%H%M%S%f}"
                sale_doc = {"id": sale_id, "invoice_no": invoice, "customer_id": int(payload["customer_id"]) if payload.get("customer_id") else None,
                    "user_id": user["id"], "subtotal": Decimal128(subtotal), "discount": Decimal128(money(payload.get("discount", 0))),
                    "tax_rate": Decimal128(Decimal(str(payload.get("tax_rate", 0)))), "tax_amount": Decimal128(tax), "total": Decimal128(total),
                    "paid_amount": Decimal128(paid), "created_at": datetime.now(timezone.utc), "items": []}
                movement_ids = []
                for line in normalized:
                    product = line["product"]
                    item_doc = {"product_id": product["id"], "name": product["name"], "sku": product["sku"], "quantity": line["quantity"],
                                "unit_price": Decimal128(line["unit_price"]), "line_total": Decimal128(money(line["unit_price"] * line["quantity"]))}
                    sale_doc["items"].append(item_doc)
                    movement_ids.append({"id": next_id(db, "inventory_movements", session), "product_id": product["id"], "movement_type": "sale",
                        "quantity": -line["quantity"], "reference": invoice, "created_at": datetime.now(timezone.utc)})
                db.sales.insert_one(sale_doc, session=session)
                if movement_ids: db.inventory_movements.insert_many(movement_ids, session=session)
                create_payment(db, "sale", sale_id, paid, payload.get("payment_method", "Cash"), invoice, session)
    except HTTPException: raise
    except Exception as exc:
        if "Transaction numbers are only allowed" in str(exc) or "replica set" in str(exc).lower():
            raise HTTPException(503, "MongoDB transactions require a replica set. Start the configured rs0 MongoDB or use an Atlas replica-set URI.")
        raise
    return serialize(sale_doc)

@router.get("/sales")
def sales(start: date | None = None, end: date | None = None, db: Database = Depends(get_db), _user=Depends(current_user)):
    docs = list(db.sales.find(date_query(start, end)).sort("created_at", -1))
    customer_ids = [x.get("customer_id") for x in docs if x.get("customer_id")]
    names = {x["id"]: x["name"] for x in db.customers.find({"id": {"$in": customer_ids}})} if customer_ids else {}
    for doc in docs: doc["customer"] = names.get(doc.get("customer_id"), "Walk-in")
    return [serialize(x) for x in docs]

@router.get("/sales/{sale_id}")
def sale_detail(sale_id: int, db: Database = Depends(get_db), _user=Depends(current_user)):
    sale = db.sales.find_one({"id": sale_id})
    if not sale: raise HTTPException(404, "Invoice not found")
    customer = db.customers.find_one({"id": sale.get("customer_id")}) if sale.get("customer_id") else None
    sale["customer"] = customer["name"] if customer else "Walk-in"
    return serialize(sale)

@router.post("/purchases")
def create_purchase(body: PurchaseCreate, db: Database = Depends(get_db), user: dict = Depends(current_user)):
    payload = body.model_dump()
    if not db.suppliers.find_one({"id": int(payload["supplier_id"])}): raise HTTPException(404, "Supplier not found")
    items = payload.get("items", [])
    if not items: raise HTTPException(422, "Add at least one item")
    total = money(sum((money(x["unit_cost"]) * int(x["quantity"]) for x in items), Decimal("0")))
    paid = money(payload.get("paid_amount", 0))
    if paid > total: raise HTTPException(422, "Paid amount cannot exceed purchase total")
    try:
        with get_client().start_session() as session:
            with session.start_transaction():
                purchase_id = next_id(db, "purchases", session)
                purchase_no = f"PUR-{datetime.now(timezone.utc):%Y%m%d-%H%M%S%f}"
                purchase_doc = {"id": purchase_id, "purchase_no": purchase_no, "supplier_id": int(payload["supplier_id"]),
                    "supplier_invoice": payload.get("supplier_invoice", ""), "total": Decimal128(total), "paid_amount": Decimal128(paid),
                    "created_at": datetime.now(timezone.utc), "items": []}
                movements = []
                for item in items:
                    product_id, qty = int(item["product_id"]), int(item["quantity"])
                    cost = money(item["unit_cost"])
                    result = db.products.update_one({"id": product_id, "active": True}, {"$inc": {"stock_qty": qty}, "$set": {"purchase_cost": Decimal128(cost)}}, session=session)
                    if result.matched_count != 1: raise HTTPException(404, f"Product {product_id} was not found")
                    product = db.products.find_one({"id": product_id}, session=session)
                    purchase_doc["items"].append({"product_id": product_id, "name": product["name"], "quantity": qty, "unit_cost": Decimal128(cost)})
                    movements.append({"id": next_id(db, "inventory_movements", session), "product_id": product_id, "movement_type": "purchase",
                        "quantity": qty, "reference": purchase_no, "created_at": datetime.now(timezone.utc)})
                db.purchases.insert_one(purchase_doc, session=session)
                if movements: db.inventory_movements.insert_many(movements, session=session)
                create_payment(db, "purchase", purchase_id, paid, payload.get("payment_method", "Cash"), purchase_no, session)
    except HTTPException: raise
    except Exception as exc:
        if "Transaction numbers are only allowed" in str(exc) or "replica set" in str(exc).lower():
            raise HTTPException(503, "MongoDB transactions require a replica set. Start the configured rs0 MongoDB or use an Atlas replica-set URI.")
        raise
    return serialize(purchase_doc)

@router.get("/purchases")
def purchases(db: Database = Depends(get_db), _user=Depends(current_user)):
    docs = list(db.purchases.find().sort("created_at", -1))
    supplier_ids = [x.get("supplier_id") for x in docs]
    names = {x["id"]: x["name"] for x in db.suppliers.find({"id": {"$in": supplier_ids}})} if supplier_ids else {}
    for doc in docs: doc["supplier"] = names.get(doc.get("supplier_id"), "Supplier")
    return [serialize(x) for x in docs]

@router.post("/repairs")
def create_repair(body: RepairCreate, db: Database = Depends(get_db), _user=Depends(current_user)):
    payload = body.model_dump()
    if not db.customers.find_one({"id": int(payload["customer_id"])}): raise HTTPException(404, "Customer not found")
    doc = store_value(payload)
    doc.update(id=next_id(db, "repairs"), order_no=f"RPR-{datetime.now(timezone.utc):%Y%m%d-%H%M%S%f}", status="Received", created_at=datetime.now(timezone.utc))
    db.repairs.insert_one(doc)
    return serialize(doc)

@router.get("/repairs")
def repairs(db: Database = Depends(get_db), _user=Depends(current_user)):
    docs = list(db.repairs.find().sort("created_at", -1))
    customer_ids = [x.get("customer_id") for x in docs]
    names = {x["id"]: x["name"] for x in db.customers.find({"id": {"$in": customer_ids}})} if customer_ids else {}
    for doc in docs: doc["customer"] = names.get(doc.get("customer_id"), "Customer")
    return [serialize(x) for x in docs]

@router.patch("/repairs/{repair_id}")
def update_repair(repair_id: int, body: dict, db: Database = Depends(get_db), _user=Depends(current_user)):
    if not db.repairs.find_one({"id": repair_id}): raise HTTPException(404, "Repair order not found")
    if body.get("status") not in ["Received", "In progress", "Ready", "Delivered", "Cancelled"]: raise HTTPException(422, "Invalid repair status")
    db.repairs.update_one({"id": repair_id}, {"$set": {"status": body["status"]}})
    return serialize(db.repairs.find_one({"id": repair_id}))

@router.get("/settings")
def get_settings(db: Database = Depends(get_db), _user=Depends(current_user)):
    return {x["key"]: x["value"] for x in db.settings.find()}

@router.put("/settings")
def put_settings(body: dict, db: Database = Depends(get_db), _user=Depends(ADMIN)):
    for key, value in body.items(): db.settings.update_one({"key": key}, {"$set": {"value": str(value)}}, upsert=True)
    return get_settings(db, _user)

@router.get("/rates")
def rates(db: Database = Depends(get_db), _user=Depends(current_user)):
    return [serialize(x) for x in db.metal_rates.find().sort("recorded_at", -1).limit(20)]

@router.get("/rates/live")
def live_rates(db: Database = Depends(get_db), _user=Depends(current_user)):
    return get_live_metal_rates(db)

@router.post("/rates")
def add_rate(body: dict, db: Database = Depends(get_db), _user=Depends(ADMIN)):
    price = money(body["price_per_gram"])
    if price < 0: raise HTTPException(422, "Rate must be nonnegative")
    doc = {"id": next_id(db, "metal_rates"), "metal_type": body["metal_type"], "purity": body.get("purity", ""),
           "price_per_gram": Decimal128(price), "recorded_at": datetime.now(timezone.utc)}
    db.metal_rates.insert_one(doc)
    return serialize(doc)

@router.get("/reports/summary")
def report_summary(start: date | None = None, end: date | None = None, db: Database = Depends(get_db), _user=Depends(current_user)):
    sales_rows = list(db.sales.find(date_query(start, end)))
    sales_total = sum((decimal_value(x["total"]) for x in sales_rows), Decimal("0"))
    tax_total = sum((decimal_value(x["tax_amount"]) for x in sales_rows), Decimal("0"))
    purchases_total = sum((decimal_value(x["total"]) for x in db.purchases.find()), Decimal("0"))
    inventory_value = sum((decimal_value(x.get("purchase_cost")) * int(x.get("stock_qty", 0)) for x in db.products.find({"active": True})), Decimal("0"))
    return {"sales_count": len(sales_rows), "sales_total": str(sales_total), "tax_total": str(tax_total),
            "purchases_total": str(purchases_total), "inventory_value": str(inventory_value),
            "sales": [serialize(x) for x in sorted(sales_rows, key=lambda x: x["created_at"], reverse=True)]}

@router.get("/reports/sales.csv")
def sales_csv(start: date | None = None, end: date | None = None, db: Database = Depends(get_db), _user=Depends(current_user)):
    rows = sorted(db.sales.find(date_query(start, end)), key=lambda x: x["created_at"], reverse=True)
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["Invoice", "Date", "Subtotal", "Discount", "Tax", "Total"])
    for row in rows:
        writer.writerow([row["invoice_no"], row["created_at"].isoformat(), decimal_value(row["subtotal"]), decimal_value(row["discount"]), decimal_value(row["tax_amount"]), decimal_value(row["total"])])
    return Response(out.getvalue(), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=sales-report.csv"})
