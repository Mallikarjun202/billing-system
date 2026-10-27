import asyncio
import dataclasses
import smtplib
import threading

from sqlalchemy import inspect, text

from app import emails, events, models
from app.billing import BillLine, generate_bill
from app.database import engine
from app.seed import seed


def _bill(client, email="live@example.com"):
    r = client.post("/api/bills", json={"customer_email": email, "items": [{"product_id": "P002", "quantity": 1}], "cash_paid": 10})
    assert r.status_code == 201
    return r.json()["id"]


# ---------- email status & resend ----------


def test_email_status_endpoint(client):
    pid = _bill(client)
    r = client.get(f"/api/purchases/{pid}/email")
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-store"
    # The background task has already run inside TestClient; SMTP is off in tests.
    assert r.json() == {"id": pid, "customer_email": "live@example.com", "email_status": "saved", "email_error": None}
    assert client.get("/api/purchases/9999/email").status_code == 404


def test_resend_invoice(client, outbox):
    pid = _bill(client)
    (outbox / f"invoice_{pid}.txt").unlink()
    r = client.post(f"/api/purchases/{pid}/email")
    assert r.status_code == 202
    assert r.json()["email_status"] == "pending"  # what it was when the response was built
    assert client.get(f"/api/purchases/{pid}/email").json()["email_status"] == "saved"
    assert (outbox / f"invoice_{pid}.txt").exists()


def test_resend_refused_while_pending(client, db):
    pid = _bill(client)
    db.get(models.Purchase, pid).email_status = models.EmailStatus.PENDING
    db.commit()
    assert client.post(f"/api/purchases/{pid}/email").status_code == 409


def test_failed_email_records_a_readable_reason(client, db, monkeypatch):
    smtp_on = dataclasses.replace(emails.settings, smtp_host="smtp.example.com", smtp_user="u", smtp_password="p")
    monkeypatch.setattr(emails, "settings", smtp_on)

    def reject(*args):
        raise smtplib.SMTPAuthenticationError(535, b"Username and Password not accepted")

    monkeypatch.setattr(emails, "_send_smtp", reject)
    pid = _bill(client)
    status = client.get(f"/api/purchases/{pid}/email").json()
    assert status["email_status"] == "failed"
    assert "App Password" in status["email_error"]
    assert "App Password" in client.get(f"/purchases/{pid}").text


def test_invoice_page_has_live_status(client):
    pid = _bill(client)
    html = client.get(f"/purchases/{pid}").text
    assert f'data-purchase="{pid}"' in html and "invoice.js" in html and "live.js" in html


# ---------- events ----------


def test_bill_publishes_events(db, monkeypatch):
    sent = []
    monkeypatch.setattr(events, "publish", lambda event, data=None: sent.append((event, data)))
    purchase = generate_bill(db, "a@example.com", [BillLine("P001", 2)], 200)
    assert [e for e, _ in sent] == ["purchase", "products", "drawer"]
    assert sent[0][1] == {"id": purchase.id, "customer_email": "a@example.com", "amount": 105, "items": 2}


def test_product_and_drawer_changes_publish_events(client, monkeypatch):
    sent = []
    monkeypatch.setattr(events, "publish", lambda event, data=None: sent.append(event))
    client.patch("/api/products/P001", json={"available_stock": 5})
    client.put("/api/denominations", json=[{"value": 500, "count": 3}])
    assert sent == ["products", "drawer"]


def test_stream_delivers_events_from_other_threads():
    async def scenario():
        gen = events.stream(lambda: asyncio.sleep(0, result=False))
        assert await anext(gen) == "retry: 3000\n\n"
        assert events.broker.subscriber_count == 1

        # Published from a worker thread, like a background email task.
        threading.Thread(target=events.publish, args=("email", {"id": 7, "status": "sent"})).start()
        message = await asyncio.wait_for(anext(gen), 2)
        assert message == 'event: email\ndata: {"id": 7, "status": "sent"}\n\n'

        events.broker.close_all()
        try:
            await asyncio.wait_for(anext(gen), 2)
        except StopAsyncIteration:
            pass
        else:
            raise AssertionError("stream should end on close_all()")
        assert events.broker.subscriber_count == 0

    asyncio.run(scenario())


def test_stream_pings_and_stops_when_client_leaves(monkeypatch):
    monkeypatch.setattr(events, "PING_SECONDS", 0.01)

    async def scenario():
        gone = False

        async def is_disconnected():
            return gone

        gen = events.stream(is_disconnected)
        await anext(gen)
        assert await anext(gen) == ": ping\n\n"
        gone = True
        try:
            await anext(gen)
        except StopAsyncIteration:
            pass
        assert events.broker.subscriber_count == 0

    asyncio.run(scenario())


# ---------- upgrade of an existing database ----------


def test_seed_adds_new_columns_to_old_databases():
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE purchases DROP COLUMN email_error"))
    assert "email_error" not in {c["name"] for c in inspect(engine).get_columns("purchases")}
    seed()
    assert "email_error" in {c["name"] for c in inspect(engine).get_columns("purchases")}
