"""Numbers for the dashboard page. Days are the server's local calendar days."""

import math
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from . import models
from .billing import DENOMINATIONS, get_denomination_stock

LOW_STOCK = 10


def _utc_naive(dt: datetime) -> datetime:
    # Purchases are stored as naive UTC (SQLite drops tzinfo).
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def _local(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc).astimezone() if dt.tzinfo is None else dt.astimezone()


def nice_ceiling(value: float) -> float:
    """Round up to a clean axis maximum: 1, 2, 2.5 or 5 x 10^n."""
    if value <= 0:
        return 100
    magnitude = 10 ** math.floor(math.log10(value))
    for step in (1, 2, 2.5, 5, 10):
        if step * magnitude >= value:
            return step * magnitude
    return 10 * magnitude


def build(db: Session) -> dict:
    now = datetime.now().astimezone()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today - timedelta(days=6)

    week = db.scalars(
        select(models.Purchase)
        .where(models.Purchase.created_at >= _utc_naive(week_start))
        .options(selectinload(models.Purchase.items))
    ).all()

    by_day = {(week_start + timedelta(days=i)).date(): {"revenue": 0.0, "bills": 0} for i in range(7)}
    today_revenue = today_items = 0
    today_bills = 0
    for p in week:
        day = _local(p.created_at).date()
        if day in by_day:
            by_day[day]["revenue"] += p.rounded_net_price
            by_day[day]["bills"] += 1
        if day == today.date():
            today_revenue += p.rounded_net_price
            today_bills += 1
            today_items += sum(i.quantity for i in p.items)

    axis_max = nice_ceiling(max(d["revenue"] for d in by_day.values()))
    chart = [
        {
            "label": "Today" if day == today.date() else day.strftime("%a"),
            "date": day.strftime("%d %b"),
            "revenue": data["revenue"],
            "bills": data["bills"],
            "pct": data["revenue"] / axis_max * 100,
            "is_today": day == today.date(),
        }
        for day, data in by_day.items()
    ]

    bill_count, revenue_total, customers = db.execute(
        select(
            func.count(models.Purchase.id),
            func.coalesce(func.sum(models.Purchase.rounded_net_price), 0),
            func.count(func.distinct(models.Purchase.customer_email)),
        )
    ).one()

    top_rows = db.execute(
        select(
            models.PurchaseItem.product_id,
            func.max(models.PurchaseItem.product_name),
            func.sum(models.PurchaseItem.quantity).label("qty"),
            func.sum(models.PurchaseItem.total_price),
        )
        .group_by(models.PurchaseItem.product_id)
        .order_by(func.sum(models.PurchaseItem.quantity).desc())
        .limit(5)
    ).all()
    top_max = max((r[2] for r in top_rows), default=0) or 1
    top_products = [
        {"product_id": pid, "name": name, "qty": qty, "revenue": revenue, "pct": qty / top_max * 100}
        for pid, name, qty, revenue in top_rows
    ]

    low_stock = db.scalars(
        select(models.Product)
        .where(models.Product.available_stock < LOW_STOCK)
        .order_by(models.Product.available_stock, models.Product.product_id)
    ).all()

    recent = db.scalars(
        select(models.Purchase)
        .options(selectinload(models.Purchase.items))
        .order_by(models.Purchase.created_at.desc(), models.Purchase.id.desc())
        .limit(6)
    ).all()

    drawer = get_denomination_stock(db)

    return {
        "today_revenue": today_revenue,
        "today_bills": today_bills,
        "today_items": today_items,
        "week_revenue": sum(d["revenue"] for d in by_day.values()),
        "bill_count": bill_count,
        "revenue_total": float(revenue_total),
        "average_bill": float(revenue_total) / bill_count if bill_count else 0.0,
        "customers": customers,
        "chart": chart,
        "chart_ticks": [axis_max, axis_max / 2, 0],
        "top_products": top_products,
        "low_stock": low_stock,
        "low_stock_limit": LOW_STOCK,
        "recent": recent,
        "drawer": [(v, drawer[v]) for v in DENOMINATIONS],
        "drawer_total": sum(v * c for v, c in drawer.items()),
    }
