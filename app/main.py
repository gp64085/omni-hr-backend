from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.redis import close_redis, init_redis
from app.core.scheduler import (
    start_background_scheduler,
    stop_background_scheduler,
)
from app.db.session import Base, engine
from app.schemas.common import StandardResponse


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Auto-create tables in development mode if not existing
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Initialize Redis connection pool
    await init_redis()

    # Start background leave accrual scheduler if enabled
    if settings.RUN_SCHEDULER:
        start_background_scheduler()

    yield

    # Stop background scheduler and clean up Redis connection on shutdown
    if settings.RUN_SCHEDULER:
        await stop_background_scheduler()
    await close_redis()


app = FastAPI(
    title=settings.PROJECT_NAME,
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# Set CORS
if settings.BACKEND_CORS_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.BACKEND_CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


# Custom Standardized Error Handlers
@app.exception_handler(StarletteHTTPException)
async def custom_http_exception_handler(request: Request, exc: StarletteHTTPException):
    detail = exc.detail
    if isinstance(detail, dict):
        code = detail.get("code", "HTTP_ERROR")
        message = detail.get("message", str(exc.detail))
        details_extra = detail.get("details", None)
    else:
        code = "HTTP_ERROR"
        message = str(exc.detail)
        details_extra = None

    return JSONResponse(
        status_code=exc.status_code,
        content=StandardResponse.fail(
            code=code, message=message, details=details_extra
        ).model_dump(mode="json", exclude_none=True),
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content=StandardResponse.fail(
            code="VALIDATION_ERROR",
            message="Request payload or parameters failed validation.",
            details={"errors": exc.errors()},
        ).model_dump(mode="json", exclude_none=True),
    )


app.include_router(api_router, prefix=settings.API_V1_STR)


@app.get("/", response_model_exclude_none=True)
async def root():
    return StandardResponse.ok(
        data={"name": settings.PROJECT_NAME, "version": "1.0.0", "docs": "/docs"}
    )
