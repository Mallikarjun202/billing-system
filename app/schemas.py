from datetime import datetime, timezone
from typing import Annotated

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_serializer, field_validator

from .billing import DENOMINATIONS

ProductCode = Annotated[
    str,
    Field(min_length=1, max_length=32, pattern=r"^[A-Za-z0-9_-]+$", examples=["P001"]),
]
Money = Annotated[float, Field(ge=0, le=10_000_000)]
TaxPercent = Annotated[float, Field(ge=0, le=100)]


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---------- Products ----------


class ProductBase(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=100)]
    available_stock: Annotated[int, Field(ge=0)]
    price: Money
    tax_percentage: TaxPercent = 0.0

    @field_validator("name")
    @classmethod
    def strip_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Name cannot be blank.")
        return v


class ProductCreate(ProductBase):
    product_id: ProductCode

    @field_validator("product_id")
    @classmethod
    def normalise_code(cls, v: str) -> str:
        return v.strip().upper()


class ProductUpdate(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=100)] | None = None
    available_stock: Annotated[int, Field(ge=0)] | None = None
    price: Money | None = None
    tax_percentage: TaxPercent | None = None

    @field_validator("name")
    @classmethod
    def strip_name(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = v.strip()
        if not v:
            raise ValueError("Name cannot be blank.")
        return v


class ProductOut(ORMModel):
    id: int
    product_id: str
    name: str
    available_stock: int
    price: float
    tax_percentage: float


# ---------- Denominations ----------


class DenominationIn(BaseModel):
    value: int
    count: Annotated[int, Field(ge=0)]

    @field_validator("value")
    @classmethod
    def known_value(cls, v: int) -> int:
        if v not in DENOMINATIONS:
            raise ValueError(f"Unsupported denomination. Allowed: {', '.join(map(str, DENOMINATIONS))}.")
        return v


class DenominationOut(ORMModel):
    value: int
    count: int


# ---------- Bills / purchases ----------


class BillItemIn(BaseModel):
    product_id: ProductCode
    quantity: Annotated[int, Field(gt=0, le=100_000)]


class BillRequest(BaseModel):
    customer_email: EmailStr
    items: Annotated[list[BillItemIn], Field(min_length=1)]
    cash_paid: Money
    denominations: list[DenominationIn] | None = Field(
        default=None,
        description="Optional: overwrite the shop's denomination stock before computing change.",
    )
    cash_notes: list[DenominationIn] | None = Field(
        default=None,
        description="Optional: the notes the customer handed over. If omitted, the fewest notes that make cash_paid are assumed.",
    )


class PurchaseItemOut(ORMModel):
    product_id: str
    product_name: str
    unit_price: float
    quantity: int
    purchase_price: float
    tax_percentage: float
    tax_payable: float
    total_price: float


class BalanceDenominationOut(ORMModel):
    value: int
    count: int


class _PurchaseTimestamps(ORMModel):
    created_at: datetime

    @field_serializer("created_at")
    def as_utc(self, v: datetime) -> str:
        # SQLite drops tzinfo; values are always stored in UTC.
        if v.tzinfo is None:
            v = v.replace(tzinfo=timezone.utc)
        return v.isoformat()


class PurchaseSummary(_PurchaseTimestamps):
    id: int
    customer_email: str
    net_price: float
    rounded_net_price: float
    item_count: int = 0

    @classmethod
    def from_purchase(cls, p) -> "PurchaseSummary":
        return cls.model_validate(p).model_copy(update={"item_count": sum(i.quantity for i in p.items)})


class EmailStatusOut(ORMModel):
    id: int
    customer_email: str
    email_status: str
    email_error: str | None = None


class PurchaseOut(_PurchaseTimestamps):
    id: int
    customer_email: str
    total_price_without_tax: float
    total_tax_payable: float
    net_price: float
    rounded_net_price: float
    cash_paid: float
    balance_amount: float
    undispensed_amount: float
    email_status: str
    email_error: str | None = None
    items: list[PurchaseItemOut]
    balance_denominations: list[BalanceDenominationOut]
