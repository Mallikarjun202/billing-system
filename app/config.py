"""Application settings, read from environment variables (and an optional .env file)."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
APP_DIR = BASE_DIR / "app"
DATA_DIR = BASE_DIR / "data"

load_dotenv(BASE_DIR / ".env")


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


@dataclass(frozen=True)
class Settings:
    app_name: str = _env("APP_NAME", "Billing System")
    database_url: str = _env("DATABASE_URL", f"sqlite:///{(DATA_DIR / 'billing.db').as_posix()}")
    sql_echo: bool = _env("SQL_ECHO", "false").lower() == "true"
    currency_symbol: str = _env("CURRENCY_SYMBOL", "₹")

    smtp_host: str = _env("SMTP_HOST")
    smtp_port: int = int(_env("SMTP_PORT", "587") or 587)
    smtp_user: str = _env("SMTP_USER")
    smtp_password: str = _env("SMTP_PASSWORD")
    smtp_from: str = _env("SMTP_FROM")
    smtp_use_ssl: bool = _env("SMTP_USE_SSL", "false").lower() == "true"

    # When SMTP is not configured, invoices are written here instead.
    email_outbox_dir: Path = Path(_env("EMAIL_OUTBOX_DIR") or DATA_DIR / "sent_emails")

    @property
    def smtp_configured(self) -> bool:
        return bool(self.smtp_host and self.smtp_user and self.smtp_password)

    @property
    def mail_from(self) -> str:
        return self.smtp_from or self.smtp_user


settings = Settings()
