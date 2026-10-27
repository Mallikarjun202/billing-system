from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class EmailStatus:
    PENDING = "pending"
    SENT = "sent"
    SAVED = "saved"  # SMTP not configured; invoice written to the outbox folder
    FAILED = "failed"


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (
        CheckConstraint("available_stock >= 0", name="ck_product_stock_non_negative"),
        CheckConstraint("price >= 0", name="ck_product_price_non_negative"),
        CheckConstraint("tax_percentage >= 0 AND tax_percentage <= 100", name="ck_product_tax_range"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(100))
    available_stock: Mapped[int] = mapped_column(Integer, default=0)
    price: Mapped[float] = mapped_column(Float)
    tax_percentage: Mapped[float] = mapped_column(Float, default=0.0)


class Purchase(Base):
    __tablename__ = "purchases"

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_email: Mapped[str] = mapped_column(String(254), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    total_price_without_tax: Mapped[float] = mapped_column(Float, default=0.0)
    total_tax_payable: Mapped[float] = mapped_column(Float, default=0.0)
    net_price: Mapped[float] = mapped_column(Float, default=0.0)
    rounded_net_price: Mapped[float] = mapped_column(Float, default=0.0)
    cash_paid: Mapped[float] = mapped_column(Float, default=0.0)
    balance_amount: Mapped[float] = mapped_column(Float, default=0.0)
    undispensed_amount: Mapped[float] = mapped_column(Float, default=0.0)
    email_status: Mapped[str] = mapped_column(String(16), default=EmailStatus.PENDING)
    email_error: Mapped[str | None] = mapped_column(String(300), default=None)

    items: Mapped[list["PurchaseItem"]] = relationship(
        back_populates="purchase", cascade="all, delete-orphan", order_by="PurchaseItem.id"
    )
    balance_denominations: Mapped[list["BalanceDenomination"]] = relationship(
        back_populates="purchase",
        cascade="all, delete-orphan",
        order_by="BalanceDenomination.value.desc()",
    )


class PurchaseItem(Base):
    """A line on a bill. Product details are copied so old bills stay correct
    even if the product is later edited or deleted."""

    __tablename__ = "purchase_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    purchase_id: Mapped[int] = mapped_column(ForeignKey("purchases.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[str] = mapped_column(String(32))
    product_name: Mapped[str] = mapped_column(String(100))
    unit_price: Mapped[float] = mapped_column(Float)
    quantity: Mapped[int] = mapped_column(Integer)
    purchase_price: Mapped[float] = mapped_column(Float)
    tax_percentage: Mapped[float] = mapped_column(Float)
    tax_payable: Mapped[float] = mapped_column(Float)
    total_price: Mapped[float] = mapped_column(Float)

    purchase: Mapped[Purchase] = relationship(back_populates="items")


class BalanceDenomination(Base):
    """How the change for a purchase was handed back (e.g. 2 x 500, 1 x 20)."""

    __tablename__ = "balance_denominations"

    id: Mapped[int] = mapped_column(primary_key=True)
    purchase_id: Mapped[int] = mapped_column(ForeignKey("purchases.id", ondelete="CASCADE"), index=True)
    value: Mapped[int] = mapped_column(Integer)
    count: Mapped[int] = mapped_column(Integer)

    purchase: Mapped[Purchase] = relationship(back_populates="balance_denominations")


class Denomination(Base):
    """Notes/coins the shop currently holds for giving change."""

    __tablename__ = "denominations"
    __table_args__ = (CheckConstraint("count >= 0", name="ck_denomination_count_non_negative"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    value: Mapped[int] = mapped_column(Integer, unique=True)
    count: Mapped[int] = mapped_column(Integer, default=0)
