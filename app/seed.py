"""Sample data so a fresh database is usable straight away.

Run directly with:  python -m app.seed
"""

from sqlalchemy import func, inspect, select, text

from . import models
from .billing import DENOMINATIONS
from .database import Base, SessionLocal, engine

SAMPLE_PRODUCTS = [
    ("P001", "Notebook", 100, 50.0, 5.0),
    ("P002", "Pen", 200, 10.0, 5.0),
    ("P003", "Backpack", 30, 800.0, 12.0),
    ("P004", "Water Bottle", 50, 150.0, 18.0),
    ("P005", "Desk Lamp", 20, 650.0, 18.0),
]

STARTING_DENOMINATIONS = {500: 10, 200: 10, 100: 20, 50: 20, 20: 20, 10: 20, 5: 20, 2: 20, 1: 20}


# Columns added after the first release: (table, column, SQL type).
_ADDED_COLUMNS = [("purchases", "email_error", "VARCHAR(300)")]


def _add_missing_columns() -> None:
    """create_all() never alters existing tables, so add new columns to older databases."""
    inspector = inspect(engine)
    with engine.begin() as conn:
        for table, column, sql_type in _ADDED_COLUMNS:
            if column not in {c["name"] for c in inspector.get_columns(table)}:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}"))


def seed() -> None:
    Base.metadata.create_all(bind=engine)
    _add_missing_columns()
    with SessionLocal() as db:
        if not db.scalar(select(func.count()).select_from(models.Product)):
            db.add_all(
                models.Product(product_id=code, name=name, available_stock=stock, price=price, tax_percentage=tax)
                for code, name, stock, price, tax in SAMPLE_PRODUCTS
            )
        if not db.scalar(select(func.count()).select_from(models.Denomination)):
            db.add_all(models.Denomination(value=v, count=STARTING_DENOMINATIONS.get(v, 0)) for v in DENOMINATIONS)
        else:
            # Databases created before the 200 and 100 notes were added get
            # empty drawer slots for them, so the drawer always has every note.
            existing = set(db.scalars(select(models.Denomination.value)))
            db.add_all(models.Denomination(value=v, count=0) for v in DENOMINATIONS if v not in existing)
        db.commit()


if __name__ == "__main__":
    seed()
    print("Database ready.")
