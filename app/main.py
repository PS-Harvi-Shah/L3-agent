import logging

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from app.api.routes import router as data_access_router
from app.config import get_settings
from app.database.connection import check_database_connection
from app.logging_config import configure_logging
from app.observability.router import router as observability_router


settings = get_settings()
configure_logging(settings)
logger = logging.getLogger(__name__)

app = FastAPI(
    title=settings.app_name,
    version="0.2.0",
)
app.include_router(data_access_router)
app.include_router(observability_router)


@app.get("/health", tags=["health"])
def health() -> dict[str, str]:
    return {
        "status": "ok",
        "service": settings.app_name,
        "environment": settings.environment,
    }


@app.get("/db-health", tags=["health"])
def db_health() -> JSONResponse:
    try:
        check_database_connection()
        return JSONResponse(status_code=200, content={"status": "ok", "database": "connected"})
    except SQLAlchemyError as exc:
        logger.exception("Database health check failed")
        detail = str(getattr(exc, "orig", exc))
        return JSONResponse(
            status_code=503,
            content={
                "status": "error",
                "database": "unavailable",
                "detail": detail,
                "hint": (
                    "Start PostgreSQL with `docker compose up -d` and verify "
                    f"connection to {settings.postgres_host}:{settings.postgres_port}/{settings.postgres_db}"
                ),
            },
        )
