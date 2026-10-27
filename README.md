# Billing System

A billing app for a small shop, built with FastAPI. You pick products, take cash, and it works out the tax, the amount due and the change. The change only uses the notes and coins actually in the drawer. The customer gets the invoice by email.

## What it does

- Billing page where you tap products to add them, adjust quantities and see the change before you submit
- Tax is calculated per item, and the final amount is rounded down to the rupee
- Change is worked out from the drawer's stock. If the drawer can't make the exact change (say it has one ₹50 and three ₹20s and you owe ₹60), it still finds a way. If it really can't, the bill records how much is short.
- A bill is saved all at once or not at all, so stock and drawer counts never get half-updated
- Invoices are emailed in the background. If email isn't set up, they're saved as files in `data/sent_emails/` instead.
- Dashboard with today's sales, a 7-day chart, best sellers and low stock
- Purchase history per customer, and printable invoices
- Pages update by themselves when something changes, so you don't need to refresh. This uses Server-Sent Events.
- Products can be added and edited from the UI or through the JSON API

## Running it

Built and tested on Python 3.14. On Windows (PowerShell):

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
copy .env.example .env
uvicorn app.main:app --reload
```

Then open http://127.0.0.1:8000. The database is created in `data/billing.db` on first run, with a few sample products.

## Email setup

Leave the SMTP settings in `.env` empty and invoices are saved to disk. To send real emails through Gmail:

1. Turn on 2-Step Verification for your Google account.
2. Create an App Password at https://myaccount.google.com/apppasswords.
3. Fill in `.env`:

```
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=you@gmail.com
SMTP_PASSWORD=your16letterpassword
SMTP_FROM=you@gmail.com
```

4. Restart the server.

`.env` is in `.gitignore`, so the password never gets committed. If sending fails, the invoice page shows the reason and lets you try again.

Other settings: `DATABASE_URL` (default is SQLite in `data/`), `CURRENCY_SYMBOL` (default ₹) and `SMTP_USE_SSL` (set it to `true` if you use port 465).

## API

The interactive docs are at http://127.0.0.1:8000/docs.

| Method | Path | What it does |
| --- | --- | --- |
| GET, POST | `/api/products` | List products, add one |
| GET, PATCH, DELETE | `/api/products/{id}` | Get, update or delete a product |
| GET, PUT | `/api/denominations` | See or set the drawer's notes and coins |
| POST | `/api/bills` | Make a bill |
| GET | `/api/purchases?email=` | A customer's purchases |
| GET | `/api/purchases/{id}` | One purchase with items and change |
| GET, POST | `/api/purchases/{id}/email` | Check the invoice email status, or resend it |
| GET | `/api/events` | Live update stream |

Example:

```bash
curl -X POST http://127.0.0.1:8000/api/bills -H "Content-Type: application/json" -d '{
  "customer_email": "customer@example.com",
  "items": [{"product_id": "P001", "quantity": 2}, {"product_id": "P003", "quantity": 1}],
  "cash_paid": 1100
}'
```

## Code layout

```
app/
  main.py        app setup
  config.py      settings from .env
  database.py    database connection
  models.py      tables
  schemas.py     request and response models
  billing.py     tax, rounding, change, saving a bill
  emails.py      invoice emails
  events.py      live updates
  dashboard.py   dashboard numbers
  seed.py        sample data
  routers/       web pages and the JSON API
  templates/     HTML and email templates
  static/        CSS and JavaScript
tests/
```

## Tests

```powershell
pytest
```

## Notes

- Live updates assume a single server process. Running several workers would need something like Redis to share the events.
- Times are stored in UTC and shown in the server's local time.
