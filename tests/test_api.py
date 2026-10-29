def test_product_crud(client):
    r = client.post(
        "/api/products",
        json={"product_id": "p010", "name": "  Stapler ", "available_stock": 5, "price": 99.5, "tax_percentage": 12},
    )
    assert r.status_code == 201, r.text
    assert r.json()["product_id"] == "P010" and r.json()["name"] == "Stapler"

    assert client.post("/api/products", json={**r.json(), "product_id": "P010"}).status_code == 409

    r = client.patch("/api/products/P010", json={"price": 120})
    assert r.status_code == 200 and r.json()["price"] == 120 and r.json()["available_stock"] == 5

    assert client.delete("/api/products/P010").status_code == 204
    assert client.get("/api/products/P010").status_code == 404


def test_product_validation(client):
    r = client.post("/api/products", json={"product_id": "X1", "name": "Bad", "available_stock": -1, "price": 10})
    assert r.status_code == 422
    r = client.post("/api/products", json={"product_id": "X1", "name": "Bad", "available_stock": 1, "price": 10, "tax_percentage": 150})
    assert r.status_code == 422


def test_bill_flow_and_email_outbox(client, outbox):
    r = client.post(
        "/api/bills",
        json={"customer_email": "Shopper@Example.com", "items": [{"product_id": "P003", "quantity": 1}], "cash_paid": 1000},
    )
    assert r.status_code == 201, r.text
    bill = r.json()
    assert bill["rounded_net_price"] == 896  # 800 + 12% tax
    assert bill["balance_amount"] == 104
    assert bill["balance_denominations"] == [{"value": 100, "count": 1}, {"value": 2, "count": 2}]
    assert bill["created_at"].endswith("+00:00")

    # Background task ran: SMTP is off in tests, so the invoice is saved to disk.
    detail = client.get(f"/api/purchases/{bill['id']}").json()
    assert detail["email_status"] == "saved"
    assert (outbox / f"invoice_{bill['id']}.html").exists()

    history = client.get("/api/purchases", params={"email": "shopper@example.com"}).json()
    assert [p["id"] for p in history] == [bill["id"]]
    assert history[0]["item_count"] == 1


def test_bill_errors_are_400_or_422(client):
    r = client.post("/api/bills", json={"customer_email": "a@b.co", "items": [{"product_id": "P001", "quantity": 1}], "cash_paid": 1})
    assert r.status_code == 400 and "less than the amount due" in r.json()["detail"]

    r = client.post("/api/bills", json={"customer_email": "not-an-email", "items": [{"product_id": "P001", "quantity": 1}], "cash_paid": 100})
    assert r.status_code == 422

    r = client.post("/api/bills", json={"customer_email": "a@b.co", "items": [], "cash_paid": 100})
    assert r.status_code == 422


def test_denomination_update_rejects_unknown_values(client):
    assert client.put("/api/denominations", json=[{"value": 3, "count": 1}]).status_code == 422
    r = client.put("/api/denominations", json=[{"value": 500, "count": 4}])
    assert r.status_code == 200 and r.json()[0] == {"value": 500, "count": 4}


def test_html_pages_render(client):
    for path in ["/", "/purchases", "/purchases?email=a@b.co", "/products"]:
        r = client.get(path)
        assert r.status_code == 200, path
        assert "<html" in r.text


def test_html_bill_form_redirects_to_invoice(client):
    r = client.post(
        "/bills",
        data={
            "customer_email": "walkin@example.com",
            "product_id": ["P001", "P002"],
            "quantity": ["2", "3"],
            "cash_paid": "200",
            "denomination_500": "10",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    page = client.get(r.headers["location"])
    assert page.status_code == 200
    assert "Notebook" in page.text and "Pen" in page.text and "Bill generated" in page.text


def test_html_bill_form_shows_errors_and_keeps_input(client):
    r = client.post(
        "/bills",
        data={"customer_email": "walkin@example.com", "product_id": ["P001"], "quantity": ["1"], "cash_paid": "5"},
    )
    assert r.status_code == 400
    assert "less than the amount due" in r.text
    assert 'value="walkin@example.com"' in r.text


def test_missing_purchase_html_and_json(client):
    assert client.get("/api/purchases/9999").json() == {"detail": "Purchase not found."}
    r = client.get("/purchases/9999", headers={"accept": "text/html"})
    assert r.status_code == 404 and "Purchase not found." in r.text


def test_dashboard_empty_and_with_sales(client):
    r = client.get("/dashboard")
    assert r.status_code == 200 and "No bills yet" in r.text

    client.post(
        "/api/bills",
        json={"customer_email": "a@example.com", "items": [{"product_id": "P003", "quantity": 2}], "cash_paid": 2000},
    )
    r = client.get("/dashboard")
    assert r.status_code == 200
    assert "Backpack" in r.text and "a@example.com" in r.text
    assert "₹1,792.00" in r.text  # today's sales: 2 x 800 + 12% tax


def test_latest_purchases_without_email(client):
    client.post("/api/bills", json={"customer_email": "x@example.com", "items": [{"product_id": "P002", "quantity": 1}], "cash_paid": 20})
    r = client.get("/purchases")
    assert r.status_code == 200 and "Latest bills" in r.text and "x@example.com" in r.text


def test_bill_with_explicit_cash_notes(client):
    body = {"customer_email": "a@example.com", "items": [{"product_id": "P001", "quantity": 1}], "cash_paid": 100}
    r = client.post("/api/bills", json={**body, "cash_notes": [{"value": 50, "count": 2}]})
    assert r.status_code == 201, r.text
    r = client.post("/api/bills", json={**body, "cash_notes": [{"value": 50, "count": 1}]})
    assert r.status_code == 400 and "add up" in r.json()["detail"]
