"""
Dahua Access Control API — main FastAPI factory.

Run:
    uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

Open http://localhost:8000/docs for the interactive Swagger UI.
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from starlette.responses import JSONResponse

from app.config import get_settings
from app.core import db
from app.core.device_pool import DevicePool
from app.core.rate_limit import limiter
from app.exceptions import register_exception_handlers
from app.logging_setup import configure_logging, new_request_id
from app.routers import attendance, auth, dghs, face_lookup, jobs, meta, migration, users

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(level=settings.log_level)
    log.info("startup", extra={"app": settings.app_name, "debug": settings.debug})

    # Initialize SQLite (creates table if missing)
    db.init_db()

    # Initialize DGHS state table
    from app.services import dghs_service
    dghs_service.init_state_table()

    # Build device pool
    pool = DevicePool()
    for dev in settings.devices:
        try:
            pool.register(dev.name, dev.ip, dev.port, dev.username, dev.password)
            log.info("device_login_ok", extra={"device": dev.name, "ip": dev.ip})
        except Exception as e:
            log.error("device_login_failed", extra={
                "device": dev.name, "ip": dev.ip, "error": str(e)})
    app.state.pool = pool

    yield

    # Shutdown
    log.info("shutdown")
    pool.shutdown()


# ---------------------------------------------------------------------------
def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version="1.0.0",
        description="REST API for Dahua ASI access controllers (users, faces, attendance, migration, jobs).",
        lifespan=lifespan,
        docs_url="/docs" if settings.debug else None,
        redoc_url="/redoc" if settings.debug else None,
    )

    # Middleware order: outermost first
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # slowapi: bind limiter to app + add middleware + handler
    app.state.limiter = limiter
    app.add_middleware(SlowAPIMiddleware)

    @app.exception_handler(RateLimitExceeded)
    async def _rate_limit_handler(request: Request, exc: RateLimitExceeded):
        return JSONResponse(
            status_code=429,
            content={"error": "rate_limit_exceeded", "detail": str(exc.detail)},
        )

    # Per-request request_id for log correlation
    @app.middleware("http")
    async def _request_id_middleware(request: Request, call_next):
        rid = new_request_id()
        try:
            response = await call_next(request)
        except Exception:
            log.exception("unhandled_exception", extra={"path": request.url.path})
            raise
        response.headers["X-Request-ID"] = rid
        return response

    register_exception_handlers(app)

    # Routers
    app.include_router(meta.router)
    app.include_router(auth.router)
    app.include_router(users.router)
    app.include_router(migration.router)
    app.include_router(attendance.router)
    app.include_router(jobs.router)
    app.include_router(dghs.router)
    app.include_router(face_lookup.router)

    return app


app = create_app()
