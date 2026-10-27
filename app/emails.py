"""Invoice emails, sent as a FastAPI background task after the response."""

import logging
import smtplib
import socket
import ssl
from email.message import EmailMessage

from sqlalchemy import select

from . import events, models
from .config import settings
from .database import SessionLocal
from .templating import templates

log = logging.getLogger(__name__)


def render_invoice(purchase: models.Purchase) -> tuple[str, str, str]:
    """Return (subject, plain-text body, HTML body)."""
    subject = f"Your invoice #{purchase.id} from {settings.app_name}"
    context = {"purchase": purchase}
    text = templates.get_template("email/invoice.txt").render(context)
    html = templates.get_template("email/invoice.html").render(context)
    return subject, text, html


def _send_smtp(to: str, subject: str, text: str, html: str) -> None:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = settings.mail_from
    msg["To"] = to
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")

    context = ssl.create_default_context()
    if settings.smtp_use_ssl:
        with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, context=context, timeout=20) as server:
            server.login(settings.smtp_user, settings.smtp_password)
            server.send_message(msg)
    else:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as server:
            server.starttls(context=context)
            server.login(settings.smtp_user, settings.smtp_password)
            server.send_message(msg)


def _save_to_outbox(purchase_id: int, to: str, subject: str, text: str, html: str) -> None:
    outbox = settings.email_outbox_dir
    outbox.mkdir(parents=True, exist_ok=True)
    (outbox / f"invoice_{purchase_id}.txt").write_text(
        f"To: {to}\nSubject: {subject}\n\n{text}", encoding="utf-8"
    )
    (outbox / f"invoice_{purchase_id}.html").write_text(html, encoding="utf-8")
    log.info("SMTP not configured; invoice %s saved to %s", purchase_id, outbox)


def describe_failure(exc: Exception) -> str:
    """A short reason someone at the till can act on."""
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return "The mail server rejected the login. Check SMTP_USER and the App Password in .env."
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        return "The mail server refused the customer's email address."
    if isinstance(exc, smtplib.SMTPSenderRefused):
        return "The mail server refused the sender address (SMTP_FROM)."
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return f"Timed out connecting to {settings.smtp_host}:{settings.smtp_port}."
    if isinstance(exc, (OSError, smtplib.SMTPConnectError, smtplib.SMTPServerDisconnected)):
        return f"Couldn't reach the mail server {settings.smtp_host}:{settings.smtp_port}."
    return (str(exc) or type(exc).__name__)[:300]


def publish_status(purchase: models.Purchase) -> None:
    events.publish(
        "email",
        {
            "id": purchase.id,
            "status": purchase.email_status,
            "error": purchase.email_error,
            "customer_email": purchase.customer_email,
        },
    )


def send_invoice_email(purchase_id: int) -> None:
    with SessionLocal() as db:
        purchase = db.scalar(select(models.Purchase).where(models.Purchase.id == purchase_id))
        if purchase is None:
            return
        try:
            subject, text, html = render_invoice(purchase)
            if settings.smtp_configured:
                _send_smtp(purchase.customer_email, subject, text, html)
                purchase.email_status = models.EmailStatus.SENT
            else:
                _save_to_outbox(purchase.id, purchase.customer_email, subject, text, html)
                purchase.email_status = models.EmailStatus.SAVED
            purchase.email_error = None
        except Exception as exc:
            log.exception("Failed to send invoice for purchase %s", purchase_id)
            purchase.email_status = models.EmailStatus.FAILED
            purchase.email_error = describe_failure(exc)
        db.commit()
        publish_status(purchase)
