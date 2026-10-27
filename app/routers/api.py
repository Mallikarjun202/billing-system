from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from pydantic import EmailStr
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from .. import billing, events, models, schemas
from ..database import get_db
from ..emails import publish_status, send_invoice_email

router = APIRouter(prefix="/api")


def _get_product(db: Session, product_id: str) -> models.Product:
    product = db.scalar(select(models.Product).where(models.Product.product_id == product_id.strip().upper()))
    if product is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Product not found.")
    return product


def _get_purchase(db: Session, purchase_id: int) -> models.Purchase:
    purchase = db.get(models.Purchase, purchase_id)
    if purchase is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Purchase not found.")
    return purchase


def _denominations(db: Session) -> list[models.Denomination]:
    return list(db.scalars(select(models.Denomination).order_by(models.Denomination.value.desc())))


# ---------------- products ----------------


@router.get("/products", response_model=list[schemas.ProductOut], tags=["products"])
def list_products(db: Session = Depends(get_db)):
    return db.scalars(select(models.Product).order_by(models.Product.product_id)).all()


@router.post("/products", response_model=schemas.ProductOut, status_code=status.HTTP_201_CREATED, tags=["products"])
def create_product(payload: schemas.ProductCreate, db: Session = Depends(get_db)):
    if db.scalar(select(models.Product.id).where(models.Product.product_id == payload.product_id)):
        raise HTTPException(status.HTTP_409_CONFLICT, f"Product ID {payload.product_id} already exists.")
    product = models.Product(**payload.model_dump())
    db.add(product)
    db.commit()
    events.publish("products")
    return product


@router.get("/products/{product_id}", response_model=schemas.ProductOut, tags=["products"])
def get_product(product_id: str, db: Session = Depends(get_db)):
    return _get_product(db, product_id)


@router.patch("/products/{product_id}", response_model=schemas.ProductOut, tags=["products"])
def update_product(product_id: str, payload: schemas.ProductUpdate, db: Session = Depends(get_db)):
    product = _get_product(db, product_id)
    for field, value in payload.model_dump(exclude_unset=True, exclude_none=True).items():
        setattr(product, field, value.strip() if isinstance(value, str) else value)
    db.commit()
    events.publish("products")
    return product


@router.delete("/products/{product_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["products"])
def delete_product(product_id: str, db: Session = Depends(get_db)):
    db.delete(_get_product(db, product_id))
    db.commit()
    events.publish("products")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------- denominations ----------------


@router.get("/denominations", response_model=list[schemas.DenominationOut], tags=["denominations"])
def list_denominations(db: Session = Depends(get_db)):
    return _denominations(db)


@router.put("/denominations", response_model=list[schemas.DenominationOut], tags=["denominations"])
def update_denominations(payload: list[schemas.DenominationIn], db: Session = Depends(get_db)):
    billing.set_denomination_stock(db, [(d.value, d.count) for d in payload])
    db.commit()
    events.publish("drawer")
    return _denominations(db)


# ---------------- bills & purchases ----------------


@router.post("/bills", response_model=schemas.PurchaseOut, status_code=status.HTTP_201_CREATED, tags=["bills"])
def create_bill(payload: schemas.BillRequest, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    try:
        purchase = billing.generate_bill(
            db,
            payload.customer_email,
            [billing.BillLine(i.product_id, i.quantity) for i in payload.items],
            payload.cash_paid,
            [(d.value, d.count) for d in payload.denominations] if payload.denominations else None,
        )
    except billing.BillingError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    background_tasks.add_task(send_invoice_email, purchase.id)
    return purchase


@router.get("/purchases", response_model=list[schemas.PurchaseSummary], tags=["bills"])
def list_purchases(email: EmailStr = Query(..., description="Customer email"), db: Session = Depends(get_db)):
    purchases = db.scalars(
        select(models.Purchase)
        .where(models.Purchase.customer_email == email.strip().lower())
        .options(selectinload(models.Purchase.items))
        .order_by(models.Purchase.created_at.desc(), models.Purchase.id.desc())
    ).all()
    return [schemas.PurchaseSummary.from_purchase(p) for p in purchases]


@router.get("/purchases/{purchase_id}", response_model=schemas.PurchaseOut, tags=["bills"])
def get_purchase(purchase_id: int, db: Session = Depends(get_db)):
    return _get_purchase(db, purchase_id)


@router.get("/purchases/{purchase_id}/email", response_model=schemas.EmailStatusOut, tags=["bills"])
def get_email_status(purchase_id: int, response: Response, db: Session = Depends(get_db)):
    """Invoice email status: pending, sent, saved (to the outbox folder) or failed."""
    response.headers["Cache-Control"] = "no-store"
    return _get_purchase(db, purchase_id)


@router.post(
    "/purchases/{purchase_id}/email",
    response_model=schemas.EmailStatusOut,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["bills"],
)
def resend_invoice(purchase_id: int, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """Send the invoice email again (e.g. after fixing the SMTP settings)."""
    purchase = _get_purchase(db, purchase_id)
    if purchase.email_status == models.EmailStatus.PENDING:
        raise HTTPException(status.HTTP_409_CONFLICT, "This invoice is already being sent.")
    purchase.email_status = models.EmailStatus.PENDING
    purchase.email_error = None
    db.commit()
    publish_status(purchase)
    background_tasks.add_task(send_invoice_email, purchase.id)
    return purchase


# ---------------- live updates ----------------


@router.get("/events", tags=["live"], response_class=StreamingResponse)
async def live_events(request: Request):
    """Server-Sent Events stream of `purchase`, `email`, `products` and `drawer` changes.

    Open it with the browser's `EventSource("/api/events")`.
    """
    return StreamingResponse(
        events.stream(request.is_disconnected),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
