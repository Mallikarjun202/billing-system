from datetime import datetime, timezone

from fastapi.templating import Jinja2Templates

from .config import APP_DIR, settings

templates = Jinja2Templates(directory=APP_DIR / "templates")


def format_money(value) -> str:
    return f"{settings.currency_symbol}{float(value or 0):,.2f}"


def format_datetime(value: datetime | None) -> str:
    if value is None:
        return ""
    if value.tzinfo is None:  # SQLite returns naive datetimes; they are UTC.
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone().strftime("%d %b %Y, %I:%M %p")


def format_percent(value) -> str:
    return f"{float(value or 0):g}%"


def format_money_short(value) -> str:
    """Compact amounts for tiles and axes: ₹950, ₹12.4K, ₹3.1L (lakh), ₹2.5Cr."""
    v = float(value or 0)
    for size, suffix in ((1e7, "Cr"), (1e5, "L"), (1e3, "K")):
        if abs(v) >= size:
            return f"{settings.currency_symbol}{v / size:.1f}".rstrip("0").rstrip(".") + suffix
    return f"{settings.currency_symbol}{v:,.0f}"


def initials(text: str) -> str:
    words = [w for w in str(text or "").replace("@", " ").replace(".", " ").split() if w]
    if not words:
        return "?"
    if len(words) == 1:
        return words[0][:2].upper()
    return (words[0][0] + words[1][0]).upper()


# Stable decorative hue per product/customer (not a data encoding).
_AVATAR_HUES = (232, 262, 292, 330, 12, 38, 160, 190)


def avatar_hue(key: str) -> int:
    return _AVATAR_HUES[sum(map(ord, str(key or ""))) % len(_AVATAR_HUES)]


templates.env.filters["money"] = format_money
templates.env.filters["money_short"] = format_money_short
templates.env.filters["initials"] = initials
templates.env.filters["hue"] = avatar_hue
templates.env.filters["datetime"] = format_datetime
templates.env.filters["percent"] = format_percent
templates.env.globals["app_name"] = settings.app_name
templates.env.globals["currency"] = settings.currency_symbol
