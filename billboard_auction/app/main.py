import logging
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import config
from .api import router
from .database import SessionLocal, init_db
from .services import ServiceError, close_due

log = logging.getLogger("billboard")
STATIC_DIR = Path(__file__).parent / "static"


def _sweeper(stop: threading.Event):
    """Closes overdue auctions when no Celery worker is running (local demos)."""
    while not stop.wait(config.SWEEP_INTERVAL_SECONDS):
        try:
            with SessionLocal() as db:
                done = close_due(db)
                if done:
                    log.info("embedded sweeper closed %s", done)
        except Exception:
            log.exception("sweeper failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    stop = threading.Event()
    thread = None
    if config.EMBEDDED_SWEEPER:
        thread = threading.Thread(target=_sweeper, args=(stop,), daemon=True, name="auction-sweeper")
        thread.start()
    yield
    stop.set()


app = FastAPI(title="Billboard Auction", version="1.0.0", lifespan=lifespan)
app.include_router(router)


@app.exception_handler(ServiceError)
async def service_error_handler(_: Request, exc: ServiceError):
    return JSONResponse(
        status_code=exc.status,
        content={"code": exc.code, "message": exc.message, "details": exc.details},
    )


@app.exception_handler(RequestValidationError)
async def validation_handler(_: Request, exc: RequestValidationError):
    first = exc.errors()[0]
    field = ".".join(str(p) for p in first["loc"] if p not in ("body", "query"))
    return JSONResponse(
        status_code=422,
        content={"code": "VALIDATION", "message": f"{field}: {first['msg']}" if field else first["msg"], "details": {}},
    )


@app.get("/healthz")
def healthz():
    return {"ok": True}


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="web")
