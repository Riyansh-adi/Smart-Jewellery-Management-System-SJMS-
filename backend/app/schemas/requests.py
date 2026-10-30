from datetime import date
from decimal import Decimal
from pydantic import BaseModel, Field, model_validator

class CustomerCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    phone: str = Field(default="", max_length=40)
    email: str = Field(default="", max_length=255)
    address: str = ""

class SupplierCreate(CustomerCreate):
    tax_id: str = Field(default="", max_length=80)

class ProductCreate(BaseModel):
    sku: str = Field(min_length=1, max_length=60)
    name: str = Field(min_length=1, max_length=180)
    category_id: int | None = None
    supplier_id: int | None = None
    metal_type: str = "Gold"
    purity: str = "22K"
    gross_weight: Decimal = Field(default=0, ge=0)
    net_weight: Decimal = Field(default=0, ge=0)
    stone_weight: Decimal = Field(default=0, ge=0)
    wastage_percent: Decimal = Field(default=0, ge=0)
    making_charge: Decimal = Field(default=0, ge=0)
    purchase_cost: Decimal = Field(default=0, ge=0)
    selling_price: Decimal = Field(default=0, ge=0)
    stock_qty: int = Field(default=0, ge=0)
    low_stock_at: int = Field(default=2, ge=0)
    barcode: str = ""

    @model_validator(mode="after")
    def valid_weights(self):
        if self.gross_weight < self.net_weight + self.stone_weight:
            raise ValueError("Gross weight must be at least net weight plus stone weight")
        return self

class SaleLine(BaseModel):
    product_id: int
    quantity: int = Field(ge=1)
    unit_price: Decimal | None = Field(default=None, ge=0)

class SaleCreate(BaseModel):
    customer_id: int | None = None
    items: list[SaleLine] = Field(min_length=1)
    discount: Decimal = Field(default=0, ge=0)
    tax_rate: Decimal = Field(default=0, ge=0)
    paid_amount: Decimal | None = Field(default=None, ge=0)
    payment_method: str = "Cash"

class PurchaseLine(BaseModel):
    product_id: int
    quantity: int = Field(ge=1)
    unit_cost: Decimal = Field(ge=0)

class PurchaseCreate(BaseModel):
    supplier_id: int
    supplier_invoice: str = ""
    items: list[PurchaseLine] = Field(min_length=1)
    paid_amount: Decimal = Field(default=0, ge=0)

class RepairCreate(BaseModel):
    customer_id: int
    description: str = Field(min_length=1)
    repair_type: str = Field(min_length=1)
    estimated_delivery: date | None = None
    charge: Decimal = Field(default=0, ge=0)

class UserCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=10)
    role: str

    @model_validator(mode="after")
    def valid_role(self):
        if self.role not in {"admin", "manager", "staff"}:
            raise ValueError("Role must be admin, manager, or staff")
        return self

