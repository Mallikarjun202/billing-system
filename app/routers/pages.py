from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from email_validator import EmailNotValidError, validate_email
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from .. import billing, dashboard, models
from ..database import get_db
from ..emails import send_invoice_email
from ..templating import templates

router = APIRouter(include_in_schema=False)


def _products(db: Session) -> list[models.Product]:
    return list(db.scalars(select(models.Product).order_by(models.Product.product_id)))


def _render_billing_form(request: Request, db: Session, form: dict | None = None, error: str | None = None):
    stock = billing.get_denomination_stock(db)
    form = form or {}
    products = _products(db)
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "products": products,
            "catalog": [
                {
                    "id": p.product_id,
                    "name": p.name,
                    "price": p.price,
                    "tax": p.tax_percentage,
                    "stock": p.available_stock,
                }
                for p in products
            ],
            "denominations": [(v, form.get("denominations", {}).get(v, stock[v])) for v in billing.DENOMINATIONS],
            "form": form,
            "error": error,
        },
        status_code=status.HTTP_400_BAD_REQUEST if error else status.HTTP_200_OK,
    )


@router.get("/", response_class=HTMLResponse)
def billing_page(request: Request, db: Session = Depends(get_db)):
    return _render_billing_form(request, db)


@router.post("/bills", response_class=HTMLResponse)
async def submit_bill(request: Request, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    data = await request.form()

    email = str(data.get("customer_email") or "").strip()
    raw_cash = str(data.get("cash_paid") or "").strip()
    rows = [
        {"product_id": str(pid or "").strip().upper(), "quantity": str(qty or "").strip()}
        for pid, qty in zip(data.getlist("product_id"), data.getlist("quantity"))
    ]
    rows = [r for r in rows if r["product_id"] or r["quantity"]]

    denominations: dict[int, int] = {}
    errors: list[str] = []
    for value in billing.DENOMINATIONS:
        raw = str(data.get(f"denomination_{value}") or "").strip()
        if not raw:
            continue
        try:
            count = int(raw)
            if count < 0:
                raise ValueError
            denominations[value] = count
        except ValueError:
            errors.append(f"Count for {value} notes/coins must be a whole number of 0 or more.")

    form = {"customer_email": email, "cash_paid": raw_cash, "rows": rows, "denominations": denominations}

    try:
        email = validate_email(email, check_deliverability=False).normalized
    except EmailNotValidError:
        errors.append("Enter a valid customer email address.")

    lines: list[billing.BillLine] = []
    for r in rows:
        if not r["product_id"]:
            errors.append("Choose a product for every row (or remove the empty row).")
            continue
        try:
            qty = int(r["quantity"])
            if qty < 1:
                raise ValueError
        except ValueError:
            errors.append(f"Quantity for {r['product_id']} must be a whole number of 1 or more.")
            continue
        lines.append(billing.BillLine(r["product_id"], qty))
    if not rows:
        errors.append("Add at least one product to the bill.")

    try:
        cash_paid = float(raw_cash)
        if cash_paid < 0:
            raise ValueError
    except ValueError:
        errors.append("Enter the cash paid as a number.")
        cash_paid = 0.0

    if errors:
        return _render_billing_form(request, db, form, " ".join(dict.fromkeys(errors)))

    try:
        purchase = billing.generate_bill(db, email, lines, cash_paid, denominations.items() or None)
    except billing.BillingError as exc:
        return _render_billing_form(request, db, form, str(exc))

    background_tasks.add_task(send_invoice_email, purchase.id)
    # Redirect so refreshing the result page can't submit the bill twice.
    return RedirectResponse(f"/purchases/{purchase.id}?new=1", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard_page(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(request, "dashboard.html", {"d": dashboard.build(db)})


@router.get("/purchases", response_class=HTMLResponse)
def purchases_page(request: Request, email: str | None = None, db: Session = Depends(get_db)):
    email = (email or "").strip().lower()
    query = (
        select(models.Purchase)
        .options(selectinload(models.Purchase.items))
        .order_by(models.Purchase.created_at.desc(), models.Purchase.id.desc())
    )
    # With an email: that customer's history. Without: the latest bills overall.
    purchases = db.scalars(query.where(models.Purchase.customer_email == email) if email else query.limit(20)).all()
    return templates.TemplateResponse(request, "purchases.html", {"email": email, "purchases": purchases})


@router.get("/purchases/{purchase_id}", response_class=HTMLResponse)
def purchase_page(purchase_id: int, request: Request, new: bool = False, db: Session = Depends(get_db)):
    purchase = db.get(models.Purchase, purchase_id)
    if purchase is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Purchase not found.")
    return templates.TemplateResponse(request, "bill.html", {"purchase": purchase, "is_new": new})


@router.get("/products", response_class=HTMLResponse)
def products_page(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(request, "products.html", {"products": _products(db)})
