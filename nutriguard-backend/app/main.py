import time
import uuid
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded

from app.api.v1.router import api_router
from app.core.body_size_limit import BodySizeLimitMiddleware
from app.core.config import settings
from app.core.exceptions import AppError, error_envelope
from app.core.logging import configure_logging, get_logger
from app.core.rate_limit import limiter
from app.core.scan_attempt import (
    SCAN_ATTEMPT_ID_HEADER,
    SCAN_REQUEST_SEQUENCE_HEADER,
    resolve_scan_attempt_context,
)

configure_logging()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("startup", environment=settings.ENVIRONMENT, version=settings.APP_VERSION)
    yield
    logger.info("shutdown")


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.PROJECT_NAME,
        version=settings.APP_VERSION,
        openapi_url=f"{settings.API_V1_PREFIX}/openapi.json",
        docs_url=f"{settings.API_V1_PREFIX}/docs",
        redoc_url=f"{settings.API_V1_PREFIX}/redoc",
        lifespan=lifespan,
    )

    app.state.limiter = limiter

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def request_context_middleware(request: Request, call_next):
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id
        start = time.perf_counter()
        structlog.contextvars.bind_contextvars(request_id=request_id, path=request.url.path)
        try:
            response = await call_next(request)
        except Exception:
            duration_ms = round((time.perf_counter() - start) * 1000, 2)
            logger.exception("unhandled_exception", duration_ms=duration_ms)
            raise
        else:
            duration_ms = round((time.perf_counter() - start) * 1000, 2)
            logger.info(
                "request_completed",
                method=request.method,
                status_code=response.status_code,
                duration_ms=duration_ms,
            )
            response.headers["X-Request-ID"] = request_id
            return response
        finally:
            structlog.contextvars.clear_contextvars()

    # Issue #30: scan-attempt correlation, scoped to exactly the three
    # scan endpoints the contract names (see docs/SCAN_ATTEMPT_DIAGNOSTICS.md)
    # -- never applied to any other route, including the diagnostics
    # ingestion endpoint itself. Header parsing happens BEFORE the route
    # runs so the accepted/generated id is available to
    # `app.api.v1.scan`'s own diagnostic-journal writes, and the
    # response header is set on this middleware's own `call_next`
    # result so it is present on success AND on every handled failure
    # (validation/auth/rate-limit/AppError all resolve to a normal
    # `JSONResponse` from an exception handler INSIDE `call_next`, not a
    # raised exception -- see `handle_app_error` etc. below).
    _scan_attempt_paths = {
        f"{settings.API_V1_PREFIX}/scan/barcode",
        f"{settings.API_V1_PREFIX}/scan/ocr-text",
        f"{settings.API_V1_PREFIX}/scan/label-image",
    }

    @app.middleware("http")
    async def scan_attempt_context_middleware(request: Request, call_next):
        if request.url.path not in _scan_attempt_paths:
            return await call_next(request)
        context = resolve_scan_attempt_context(
            request.headers.get(SCAN_ATTEMPT_ID_HEADER),
            request.headers.get(SCAN_REQUEST_SEQUENCE_HEADER),
        )
        request.state.scan_attempt_context = context
        response = await call_next(request)
        response.headers[SCAN_ATTEMPT_ID_HEADER] = context.attempt_id
        return response

    # Codex review round 3: registered LAST (deliberately, not merely by
    # convention) -- `Starlette.add_middleware` prepends to its own
    # middleware list, so the most-recently-added middleware ends up
    # OUTERMOST, wrapping every middleware added above. This one must be
    # outermost: it enforces a byte cap on the raw ASGI request body
    # before Starlette's routing -- and therefore FastAPI's own
    # body-buffering -- ever runs, which only holds if nothing upstream
    # of it (CORS, the two above) gets a chance to touch the request
    # first. See `app.core.body_size_limit` for why a route-level
    # dependency could not provide this guarantee.
    app.add_middleware(BodySizeLimitMiddleware)

    # --- Exception handlers -------------------------------------------------

    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError):
        logger.warning("app_error", code=exc.code, message=exc.message)
        return JSONResponse(
            status_code=exc.status_code,
            content=error_envelope(exc.code, exc.message, exc.details),
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(request: Request, exc: RequestValidationError):
        # A custom `@field_validator` that raises a plain `ValueError`
        # (e.g. issue #30's `app.schemas.scan_diagnostics.
        # _validate_metrics`) puts that exception OBJECT in each error's
        # `ctx` by default, which is not JSON-serializable and would
        # crash this handler itself (turning a normal 422 into an
        # unhandled 500) -- FastAPI's own `RequestValidationError.errors()`
        # (unlike the underlying pydantic `ValidationError`) takes no
        # `include_context` argument, so `ctx` is dropped here instead;
        # `msg` already carries the human-readable message. See
        # tests/integration/test_scan_diagnostics_client_events.py.
        errors = [{k: v for k, v in error.items() if k != "ctx"} for error in exc.errors()]
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=error_envelope("VALIDATION_ERROR", "Request validation failed.", errors),
        )

    @app.exception_handler(RateLimitExceeded)
    async def handle_rate_limit(request: Request, exc: RateLimitExceeded):
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content=error_envelope("RATE_LIMIT_EXCEEDED", "Too many requests. Please slow down."),
        )

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception):
        logger.exception("unexpected_error")
        # Never leak stack traces, SQL errors, or internal details to clients.
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=error_envelope("INTERNAL_ERROR", "An unexpected error occurred."),
        )

    app.include_router(api_router, prefix=settings.API_V1_PREFIX)

    @app.get("/health", tags=["meta"])
    async def health_check():
        return {"status": "ok", "version": settings.APP_VERSION}

    return app


app = create_app()
