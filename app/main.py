import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import events
from .config import APP_DIR, settings
from .routers import api, pages
from .seed import seed
from .templating import templates

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    seed()  # creates tables and sample data on first run
    events.install_shutdown_hook()
    yield
    events.broker.close_all()


app = FastAPI(
    title=settings.app_name,
    description="Point-of-sale billing with tax, rounding, change calculation and emailed invoices.",
    version="2.0.0",
    lifespan=lifespan,
)
app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
app.include_router(pages.router)
app.include_router(api.router)


@app.exception_handler(StarletteHTTPException)
async def friendly_errors(request: Request, exc: StarletteHTTPException):
    """JSON errors for the API, a proper page for people using the website."""
    if request.url.path.startswith("/api") or "text/html" not in request.headers.get("accept", ""):
        return await http_exception_handler(request, exc)
    return templates.TemplateResponse(
        request,
        "error.html",
        {"status_code": exc.status_code, "detail": exc.detail},
        status_code=exc.status_code,
    )
