"""Billing rules: pricing, tax, rounding and giving change.

All money maths is done with Decimal (rounded half-up to 2 places) so totals
never drift by a paisa the way binary floats can.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import events, models

# Notes/coins the shop gives change in, largest first.
DENOMINATIONS: tuple[int, ...] = (500, 50, 20, 10, 5, 2, 1)

# Above this many rupees of change, skip the exact search and use greedy only.
_EXACT_CHANGE_LIMIT = 200_000

PAISA = Decimal("0.01")


class BillingError(ValueError):
    """A bill could not be generated because of invalid input or stock."""


@dataclass(frozen=True)
class BillLine:
    product_id: str
    quantity: int


def money(value) -> Decimal:
    return Decimal(str(value)).quantize(PAISA, rounding=ROUND_HALF_UP)


# ---------------- change ----------------


def _greedy_change(amount: int, stock: Mapping[int, int]) -> dict[int, int]:
    breakdown: dict[int, int] = {}
    for value in DENOMINATIONS:
        if amount <= 0:
            break
        use = min(amount // value, stock.get(value, 0))
        if use:
            breakdown[value] = use
            amount -= use * value
    return breakdown


def _exact_change(amount: int, stock: Mapping[int, int]) -> dict[int, int]:
    """Largest amount <= `amount` that the stock can make, using the fewest
    notes/coins. Bounded knapsack via binary splitting of each count."""
    parts: list[tuple[int, int, int]] = []  # (total value, pieces, denomination)
    for value in DENOMINATIONS:
        count, chunk = stock.get(value, 0), 1
        while count > 0:
            take = min(chunk, count)
            parts.append((value * take, take, value))
            count -= take
            chunk *= 2

    inf = float("inf")
    best = [inf] * (amount + 1)
    best[0] = 0
    used = []  # used[i][x] == 1 when part i was taken to reach x
    for part_value, pieces, _ in parts:
        took = bytearray(amount + 1)
        for x in range(amount, part_value - 1, -1):
            candidate = best[x - part_value] + pieces
            if candidate < best[x]:
                best[x] = candidate
                took[x] = 1
        used.append(took)

    reachable = next(x for x in range(amount, -1, -1) if best[x] != inf)
    breakdown: dict[int, int] = {}
    x = reachable
    for i in range(len(parts) - 1, -1, -1):
        if x and used[i][x]:
            part_value, pieces, value = parts[i]
            breakdown[value] = breakdown.get(value, 0) + pieces
            x -= part_value
    return dict(sorted(breakdown.items(), reverse=True))


def calculate_change(balance: Decimal | float, stock: Mapping[int, int]) -> tuple[dict[int, int], Decimal]:
    """Split `balance` into the shop's available notes/coins.

    Only whole rupees can be handed back. Greedy (largest first) is tried
    first; if limited stock makes it fall short (e.g. 60 with one 50 and three
    20s), an exact search finds the best combination that actually exists.

    Returns (breakdown {value: count}, amount that could not be dispensed).
    """
    balance = money(balance)
    whole = int(balance.to_integral_value(rounding=ROUND_FLOOR))
    if whole <= 0:
        return {}, balance

    total_available = sum(v * stock.get(v, 0) for v in DENOMINATIONS)
    target = min(whole, total_available)

    breakdown = _greedy_change(target, stock)
    if sum(v * c for v, c in breakdown.items()) != target and target <= _EXACT_CHANGE_LIMIT:
        breakdown = _exact_change(target, stock)

    dispensed = sum(v * c for v, c in breakdown.items())
    return breakdown, balance - dispensed


# ---------------- stock ----------------


def get_denomination_stock(db: Session) -> dict[int, int]:
    rows = db.scalars(select(models.Denomination)).all()
    stock = {d.value: d.count for d in rows}
    return {v: stock.get(v, 0) for v in DENOMINATIONS}


def set_denomination_stock(db: Session, counts: Iterable[tuple[int, int]]) -> None:
    """Overwrite the count of each given denomination. Does not commit."""
    existing = {d.value: d for d in db.scalars(select(models.Denomination)).all()}
    for value, count in counts:
        if value not in DENOMINATIONS:
            raise BillingError(f"Unsupported denomination: {value}.")
        if count < 0:
            raise BillingError(f"Count for {value} cannot be negative.")
        if value in existing:
            existing[value].count = count
        else:
            row = models.Denomination(value=value, count=count)
            db.add(row)
            existing[value] = row
    db.flush()


# ---------------- bill ----------------


def generate_bill(
    db: Session,
    customer_email: str,
    lines: Iterable[BillLine],
    cash_paid: float | Decimal,
    denomination_stock: Iterable[tuple[int, int]] | None = None,
) -> models.Purchase:
    """Create a purchase, deduct product and change stock, all in one
    transaction. Nothing is saved if any check fails."""
    try:
        if denomination_stock is not None:
            set_denomination_stock(db, denomination_stock)

        # Merge repeated products so stock is checked against the total asked for.
        quantities: dict[str, int] = {}
        for line in lines:
            code = line.product_id.strip().upper()
            if line.quantity <= 0:
                raise BillingError(f"Quantity for {code} must be at least 1.")
            quantities[code] = quantities.get(code, 0) + line.quantity
        if not quantities:
            raise BillingError("Add at least one product to the bill.")

        products = {
            p.product_id: p
            for p in db.scalars(select(models.Product).where(models.Product.product_id.in_(quantities)))
        }
        missing = [code for code in quantities if code not in products]
        if missing:
            raise BillingError(f"Unknown product ID: {', '.join(missing)}.")
        for code, qty in quantities.items():
            p = products[code]
            if qty > p.available_stock:
                raise BillingError(
                    f"Not enough stock for {p.name} ({code}): {p.available_stock} available, {qty} requested."
                )

        subtotal = tax_total = Decimal("0")
        items: list[models.PurchaseItem] = []
        for code, qty in quantities.items():
            p = products[code]
            price = money(Decimal(str(p.price)) * qty)
            tax = money(price * Decimal(str(p.tax_percentage)) / 100)
            subtotal += price
            tax_total += tax
            items.append(
                models.PurchaseItem(
                    product_id=p.product_id,
                    product_name=p.name,
                    unit_price=float(money(p.price)),
                    quantity=qty,
                    purchase_price=float(price),
                    tax_percentage=p.tax_percentage,
                    tax_payable=float(tax),
                    total_price=float(price + tax),
                )
            )

        net = subtotal + tax_total
        rounded_net = net.to_integral_value(rounding=ROUND_FLOOR)
        cash = money(cash_paid)
        if cash < rounded_net:
            raise BillingError(
                f"Cash paid ({cash:.2f}) is less than the amount due ({rounded_net:.2f})."
            )

        balance = cash - rounded_net
        breakdown, undispensed = calculate_change(balance, get_denomination_stock(db))

        purchase = models.Purchase(
            customer_email=customer_email.strip().lower(),
            total_price_without_tax=float(subtotal),
            total_tax_payable=float(tax_total),
            net_price=float(net),
            rounded_net_price=float(rounded_net),
            cash_paid=float(cash),
            balance_amount=float(balance),
            undispensed_amount=float(undispensed),
            items=items,
            balance_denominations=[
                models.BalanceDenomination(value=v, count=c) for v, c in breakdown.items()
            ],
        )
        db.add(purchase)

        for code, qty in quantities.items():
            products[code].available_stock -= qty
        if breakdown:
            for d in db.scalars(select(models.Denomination).where(models.Denomination.value.in_(breakdown))):
                d.count -= breakdown[d.value]

        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(purchase)
    events.publish(
        "purchase",
        {
            "id": purchase.id,
            "customer_email": purchase.customer_email,
            "amount": purchase.rounded_net_price,
            "items": sum(i.quantity for i in purchase.items),
        },
    )
    events.publish("products")  # stock went down
    events.publish("drawer")
    return purchase
