from decimal import Decimal

import pytest

from app import billing, models
from app.billing import BillLine, BillingError, calculate_change, generate_bill


class TestCalculateChange:
    def test_greedy_when_stock_is_plentiful(self):
        stock = {v: 100 for v in billing.DENOMINATIONS}
        breakdown, left = calculate_change(Decimal("588"), stock)
        assert breakdown == {500: 1, 50: 1, 20: 1, 10: 1, 5: 1, 2: 1, 1: 1}
        assert left == 0

    def test_finds_exact_change_greedy_would_miss(self):
        # Greedy takes the 50 and is stuck with 10; three 20s work.
        stock = {50: 1, 20: 3}
        breakdown, left = calculate_change(Decimal("60"), stock)
        assert breakdown == {20: 3}
        assert left == 0

    def test_reports_what_cannot_be_dispensed(self):
        breakdown, left = calculate_change(Decimal("1003"), {500: 2, 2: 1})
        assert breakdown == {500: 2, 2: 1}
        assert left == Decimal("1")

    def test_paise_are_never_dispensed(self):
        breakdown, left = calculate_change(Decimal("10.75"), {10: 5})
        assert breakdown == {10: 1}
        assert left == Decimal("0.75")

    def test_zero_balance(self):
        assert calculate_change(Decimal("0"), {1: 10}) == ({}, Decimal("0.00"))


class TestGenerateBill:
    def test_totals_tax_and_rounding(self, db):
        # Notebook 50 @5% x3 = 150 + 7.50; Water Bottle 150 @18% x1 = 150 + 27
        purchase = generate_bill(db, "A@Example.com", [BillLine("P001", 3), BillLine("p004", 1)], 400)
        assert purchase.customer_email == "a@example.com"
        assert purchase.total_price_without_tax == 300
        assert purchase.total_tax_payable == 34.5
        assert purchase.net_price == 334.5
        assert purchase.rounded_net_price == 334
        assert purchase.balance_amount == 66
        assert {(d.value, d.count) for d in purchase.balance_denominations} == {(50, 1), (10, 1), (5, 1), (1, 1)}
        assert purchase.undispensed_amount == 0

    def test_deducts_product_and_drawer_stock(self, db):
        # due 105, paid 600 (assumed 500 + 100), change 495 = 200 + 200 + 50 + 20 + 20 + 5
        generate_bill(db, "a@example.com", [BillLine("P001", 2)], 600)
        db.expire_all()
        assert db.query(models.Product).filter_by(product_id="P001").one().available_stock == 98
        stock = billing.get_denomination_stock(db)
        assert stock[500] == 10 + 1 and stock[100] == 20 + 1  # the customer's cash went in
        assert stock[200] == 10 - 2 and stock[50] == 20 - 1 and stock[20] == 20 - 2 and stock[5] == 20 - 1

    def test_customers_cash_goes_into_the_drawer(self, db):
        def drawer_total():
            db.expire_all()
            return sum(v * c for v, c in billing.get_denomination_stock(db).items())

        before = drawer_total()
        purchase = generate_bill(db, "a@example.com", [BillLine("P001", 1)], 1000)  # due 52
        # Drawer grows by exactly what the shop kept: cash in minus change out.
        assert drawer_total() == before + purchase.rounded_net_price

    def test_cashier_can_say_which_notes_were_handed_over(self, db):
        generate_bill(db, "a@example.com", [BillLine("P001", 1)], 100, cash_notes={50: 2})  # due 52
        db.expire_all()
        assert billing.get_denomination_stock(db)[50] == 20 + 2 - 0  # change 48 = 20+20+5+2+1
        with pytest.raises(BillingError, match="don't add up"):
            generate_bill(db, "a@example.com", [BillLine("P001", 1)], 100, cash_notes={50: 1})

    def test_split_cash_uses_fewest_notes(self):
        assert billing.split_cash(Decimal("1886.75")) == {500: 3, 200: 1, 100: 1, 50: 1, 20: 1, 10: 1, 5: 1, 1: 1}

    def test_repeated_product_lines_are_merged_for_stock_check(self, db):
        # Desk Lamp has 20 in stock: 15 + 10 must be rejected even though each line fits.
        with pytest.raises(BillingError, match="Not enough stock"):
            generate_bill(db, "a@example.com", [BillLine("P005", 15), BillLine("P005", 10)], 1_000_000)

    def test_failed_bill_changes_nothing(self, db):
        with pytest.raises(BillingError, match="less than the amount due"):
            generate_bill(db, "a@example.com", [BillLine("P001", 1)], 10, denomination_stock=[(500, 0)])
        assert db.query(models.Purchase).count() == 0
        assert billing.get_denomination_stock(db)[500] == 10  # drawer update rolled back
        assert db.query(models.Product).filter_by(product_id="P001").one().available_stock == 100

    def test_unknown_product(self, db):
        with pytest.raises(BillingError, match="Unknown product ID: NOPE"):
            generate_bill(db, "a@example.com", [BillLine("NOPE", 1)], 100)

    def test_each_purchase_gets_its_own_timestamp(self, db):
        import time

        first = generate_bill(db, "a@example.com", [BillLine("P002", 1)], 20)
        time.sleep(0.01)
        second = generate_bill(db, "a@example.com", [BillLine("P002", 1)], 20)
        assert second.created_at > first.created_at


def test_dashboard_numbers(db):
    from app import dashboard

    generate_bill(db, "a@example.com", [BillLine("P001", 2)], 200)  # due 105
    generate_bill(db, "b@example.com", [BillLine("P002", 1)], 20)  # due 10
    d = dashboard.build(db)
    assert d["today_revenue"] == 115 and d["today_bills"] == 2 and d["customers"] == 2
    assert d["chart"][-1]["is_today"] and d["chart"][-1]["revenue"] == 115
    assert d["top_products"][0]["product_id"] == "P001"
    assert dashboard.nice_ceiling(115) == 200 and dashboard.nice_ceiling(0) == 100


def test_old_database_gets_new_note_slots(db):
    # A drawer created before 200 and 100 notes existed
    db.query(models.Denomination).filter(models.Denomination.value.in_([200, 100])).delete()
    db.commit()
    from app.seed import seed

    seed()
    db.expire_all()
    stock = billing.get_denomination_stock(db)
    assert stock[200] == 0 and stock[100] == 0
    assert db.query(models.Denomination).count() == len(billing.DENOMINATIONS)
