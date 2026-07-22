import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.api.routes import router as agent_router
from app.config import get_settings
from app.logging_config import configure_logging
from app.mcp_client import MCPClient, MCPClientError


settings = get_settings()
configure_logging(settings)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    client = MCPClient(settings)
    try:
        client.connect()
    except MCPClientError:
        # Start anyway so /health and /mcp-health can report the problem;
        # agent queries will fail with a clear error until the server is up.
        logger.exception("MCP server connection failed at startup")
    app.state.mcp_client = client
    try:
        yield
    finally:
        client.close()


app = FastAPI(
    title=settings.app_name,
    version="2.0.0",
    lifespan=lifespan,
)
app.include_router(agent_router)


@app.get("/health", tags=["health"])
def health() -> dict[str, str]:
    return {
        "status": "ok",
        "service": settings.app_name,
        "environment": settings.environment,
    }


@app.get("/mcp-health", tags=["health"])
def mcp_health() -> JSONResponse:
    client: MCPClient = app.state.mcp_client
    try:
        tools = client.list_tools()
        return JSONResponse(
            status_code=200,
            content={
                "status": "ok",
                "mcp_server": "connected",
                "transport": settings.mcp_transport,
                "tools": [t.name for t in tools],
                "schema_summary": client.schema_summary,
            },
        )
    except MCPClientError as exc:
        logger.exception("MCP health check failed")
        return JSONResponse(
            status_code=503,
            content={
                "status": "error",
                "mcp_server": "unavailable",
                "detail": str(exc),
                "hint": (
                    "Start the Postgres MCP server (e.g. `postgres-mcp "
                    "--access-mode=restricted` with DATABASE_URI set) and "
                    "restart this API."
                ),
            },
        )
