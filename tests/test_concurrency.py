"""Two tills selling the last item at the same moment must not both succeed."""

import threading

from sqlalchemy import select

from app import billing, models
from app.billing import BillLine, BillingError
from app.database import SessionLocal


def test_last_item_cannot_be_sold_twice(monkeypatch):
    with SessionLocal() as s:
        s.get(models.Product, s.scalar(select(models.Product.id).where(models.Product.product_id == "P001"))).available_stock = 1
        s.commit()

    tills = 8
    # Hold every till right after its stock check and before it writes anything,
    # so all of them believe one item is left. split_cash runs exactly there.
    barrier = threading.Barrier(tills)
    real_split = billing.split_cash

    def split_after_everyone_checked(amount):
        barrier.wait(timeout=10)
        return real_split(amount)

    monkeypatch.setattr(billing, "split_cash", split_after_everyone_checked)

    sold, rejected = [], []

    def till():
        with SessionLocal() as s:
            try:
                billing.generate_bill(s, "x@example.com", [BillLine("P001", 1)], 100)
                sold.append(1)
            except BillingError:
                rejected.append(1)

    threads = [threading.Thread(target=till) for _ in range(tills)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    with SessionLocal() as s:
        left = s.scalar(select(models.Product.available_stock).where(models.Product.product_id == "P001"))
        bills = s.query(models.Purchase).count()

    assert len(sold) == 1 and len(rejected) == tills - 1
    assert left == 0 and bills == 1
