"""Synchronous facade over an external MCP server.

The application is an MCP *client*: all SQL now runs on an externally
managed Postgres MCP server (e.g. ``postgres-mcp``). The MCP SDK is
asyncio-based while the whole agent stack is synchronous, so a dedicated
background thread owns the event loop and the open session; the public
methods block on ``run_coroutine_threadsafe``.

The client never hardcodes the server's tool names — it discovers them at
connect time. It also introspects the database schema once (through the
server's own SQL tool) so the agent can be handed a compact table/column/FK
summary instead of exploring the schema turn by turn.
"""

import ast
import asyncio
import json
import logging
import re
import shlex
import shutil
import sys
import threading
from pathlib import Path
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from app.config import Settings, get_settings


logger = logging.getLogger("mcp.client")

_CONNECT_TIMEOUT = 60.0
_CALL_TIMEOUT = 120.0

# How we recognize the server's raw-SQL tool without hardcoding its name:
# a string parameter with one of these names.
_SQL_PARAM_NAMES = ("sql", "query", "statement")


class MCPClientError(Exception):
    """The MCP server could not be reached or refused the operation."""


@dataclass
class MCPToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass
class MCPCallResult:
    success: bool
    rows: list[dict[str, Any]] = field(default_factory=list)
    text: str = ""
    error: str | None = None

    @property
    def count(self) -> int:
        if self.rows:
            return len(self.rows)
        return 1 if self.text.strip() else 0


class MCPClient:
    """One shared, long-lived connection to the configured MCP server."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._session: ClientSession | None = None
        self._tools: list[MCPToolSpec] = []
        self._sql_tool: tuple[str, str] | None = None  # (tool name, sql param name)
        self._schema_summary: str | None = None
        self._ready = threading.Event()
        self._connect_error: BaseException | None = None
        self._shutdown: asyncio.Event | None = None
        self._lifecycle_future: Any = None

    # -- lifecycle -------------------------------------------------------------

    def connect(self) -> None:
        if self._session is not None:
            return
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._loop.run_forever, name="mcp-client-loop", daemon=True
        )
        self._thread.start()
        self._ready.clear()
        self._connect_error = None
        self._lifecycle_future = asyncio.run_coroutine_threadsafe(
            self._lifecycle(), self._loop
        )
        if not self._ready.wait(timeout=_CONNECT_TIMEOUT):
            self.close()
            raise MCPClientError(
                f"MCP server did not become ready within {_CONNECT_TIMEOUT:.0f}s"
            )
        if self._connect_error is not None:
            error = self._connect_error
            self.close()
            hint = ""
            if self._settings.mcp_transport.strip().lower() == "stdio" and isinstance(
                error, (FileNotFoundError, OSError)
            ):
                hint = (
                    f" — could not launch '{self._settings.mcp_server_command}'. "
                    "Check it's installed in the Python environment running this "
                    "app (`pip install -r requirements.txt` inside .venv), and "
                    "that .venv is activated before running uvicorn, or run via "
                    "'.venv\\Scripts\\python.exe -m uvicorn ...'."
                )
            raise MCPClientError(
                f"Could not connect to the MCP server: {error}{hint}"
            ) from error
        logger.info(
            "MCP server connected",
            extra={
                "transport": self._settings.mcp_transport,
                "tools": [t.name for t in self._tools],
                "sql_tool": self._sql_tool[0] if self._sql_tool else None,
            },
        )
        if self._settings.mcp_schema_in_prompt:
            self._schema_summary = self._introspect_schema()

    def close(self) -> None:
        if self._loop is None:
            return
        if self._shutdown is not None and self._session is not None:
            self._loop.call_soon_threadsafe(self._shutdown.set)
            try:
                self._lifecycle_future.result(timeout=10.0)
            except Exception:
                logger.warning("MCP session did not close cleanly", exc_info=True)
        self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread is not None:
            self._thread.join(timeout=5.0)
        self._loop = None
        self._thread = None
        self._session = None
        self._shutdown = None
        self._lifecycle_future = None

    async def _lifecycle(self) -> None:
        """Own the whole session lifetime inside ONE task.

        anyio cancel scopes (used by the SDK transports) must be entered and
        exited by the same task, so connecting and unwinding both happen here;
        ``close()`` merely signals the shutdown event.
        """
        try:
            async with AsyncExitStack() as stack:
                await self._open_session(stack)
                self._shutdown = asyncio.Event()
                self._ready.set()
                await self._shutdown.wait()
        except BaseException as exc:
            self._connect_error = exc
            self._ready.set()
        finally:
            self._session = None

    # -- public API --------------------------------------------------------------

    def list_tools(self) -> list[MCPToolSpec]:
        self._ensure_connected()
        return list(self._tools)

    @property
    def schema_summary(self) -> str | None:
        return self._schema_summary

    @property
    def sql_tool(self) -> tuple[str, str] | None:
        """(tool_name, sql_parameter_name) of the server's raw-SQL tool."""
        return self._sql_tool

    def call_tool(self, name: str, arguments: dict[str, Any]) -> MCPCallResult:
        self._ensure_connected()
        try:
            result = self._run(
                self._session.call_tool(name, arguments), timeout=_CALL_TIMEOUT
            )
        except Exception as exc:
            raise MCPClientError(f"MCP call '{name}' failed: {exc}") from exc

        text = "\n".join(
            block.text for block in result.content if getattr(block, "text", None)
        )
        if result.isError:
            return MCPCallResult(success=False, text=text, error=text or "Tool call failed")
        return MCPCallResult(success=True, rows=_parse_rows(text), text=text)

    # -- internals -----------------------------------------------------------------

    def _ensure_connected(self) -> None:
        if self._session is None:
            raise MCPClientError("MCP client is not connected. Call connect() first.")

    def _run(self, coro: Any, timeout: float) -> Any:
        assert self._loop is not None
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout=timeout)

    async def _open_session(self, stack: AsyncExitStack) -> None:
        transport = self._settings.mcp_transport.strip().lower()
        if transport == "stdio":
            args = shlex.split(self._settings.mcp_server_args)
            params = StdioServerParameters(
                command=_resolve_command(self._settings.mcp_server_command),
                args=args,
                env={"DATABASE_URI": self._settings.resolved_mcp_database_uri},
            )
            read, write = await stack.enter_async_context(stdio_client(params))
        elif transport in ("http", "streamable-http"):
            from mcp.client.streamable_http import streamablehttp_client

            read, write, _ = await stack.enter_async_context(
                streamablehttp_client(self._settings.mcp_server_url)
            )
        elif transport == "sse":
            from mcp.client.sse import sse_client

            read, write = await stack.enter_async_context(
                sse_client(self._settings.mcp_server_url)
            )
        else:
            raise ValueError(
                f"MCP_TRANSPORT must be stdio, http or sse — got '{transport}'"
            )

        self._session = await stack.enter_async_context(ClientSession(read, write))
        await self._session.initialize()

        listing = await self._session.list_tools()
        self._tools = [
            MCPToolSpec(
                name=tool.name,
                description=tool.description or "",
                input_schema=tool.inputSchema or {"type": "object", "properties": {}},
            )
            for tool in listing.tools
        ]
        self._sql_tool = _find_sql_tool(self._tools)

    def _introspect_schema(self) -> str | None:
        """One information_schema query -> compact table/column/FK summary."""
        if self._sql_tool is None:
            logger.warning("MCP server exposes no raw-SQL tool; schema summary skipped")
            return None
        tool_name, param = self._sql_tool
        query = _SCHEMA_QUERY.format(schema=self._settings.mcp_schema_name)
        try:
            result = self.call_tool(tool_name, {param: query})
        except MCPClientError:
            logger.warning("Schema introspection failed; agent will discover schema itself")
            return None
        if not result.success or not result.rows:
            logger.warning(
                "Schema introspection returned no rows",
                extra={"error": result.error, "text": result.text[:500]},
            )
            return None
        summary = _render_schema_summary(result.rows, self._settings.mcp_schema_name)
        logger.info("Database schema introspected", extra={"summary": summary})
        return summary


_SCHEMA_QUERY = """
SELECT
  c.table_name,
  c.column_name,
  c.data_type,
  (SELECT ccu.table_name || '.' || ccu.column_name
     FROM information_schema.table_constraints tc
     JOIN information_schema.key_column_usage kcu
       ON kcu.constraint_name = tc.constraint_name
      AND kcu.table_schema = tc.table_schema
     JOIN information_schema.constraint_column_usage ccu
       ON ccu.constraint_name = tc.constraint_name
      AND ccu.table_schema = tc.table_schema
    WHERE tc.constraint_type = 'FOREIGN KEY'
      AND tc.table_schema = c.table_schema
      AND kcu.table_name = c.table_name
      AND kcu.column_name = c.column_name
    LIMIT 1) AS fk_ref,
  EXISTS (SELECT 1
     FROM information_schema.table_constraints tc
     JOIN information_schema.key_column_usage kcu
       ON kcu.constraint_name = tc.constraint_name
      AND kcu.table_schema = tc.table_schema
    WHERE tc.constraint_type = 'PRIMARY KEY'
      AND tc.table_schema = c.table_schema
      AND kcu.table_name = c.table_name
      AND kcu.column_name = c.column_name) AS is_pk
FROM information_schema.columns c
WHERE c.table_schema = '{schema}'
ORDER BY c.table_name, c.ordinal_position
""".strip()


_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _resolve_command(command: str) -> str:
    """Find the server executable regardless of which Python launched the app.

    Checked in order: PATH (respects an activated venv), the Scripts/bin dir
    of whatever interpreter is running (handles ``.venv\\Scripts\\python.exe
    -m uvicorn ...`` without activation), then the project's own ``.venv``
    by relative path (handles a GLOBAL Python/uvicorn on PATH launching the
    app while the tool is only installed in the project's venv — e.g.
    running bare ``uvicorn ...`` in a shell where the venv was never
    activated, so ``sys.executable`` is the global interpreter too).
    """
    if shutil.which(command):
        return command
    candidates = [
        Path(sys.executable).parent / f"{command}.exe",
        Path(sys.executable).parent / command,
        _PROJECT_ROOT / ".venv" / "Scripts" / f"{command}.exe",
        _PROJECT_ROOT / ".venv" / "bin" / command,
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return command


def _find_sql_tool(tools: list[MCPToolSpec]) -> tuple[str, str] | None:
    """Locate the tool that EXECUTES raw SQL.

    Several tools may accept a SQL string (execute_sql, explain_query, ...);
    candidates are ranked so execution tools beat inspection tools.
    """
    candidates: list[tuple[int, str, str]] = []
    for tool in tools:
        properties = tool.input_schema.get("properties", {})
        for param in _SQL_PARAM_NAMES:
            spec = properties.get(param)
            if not (isinstance(spec, dict) and spec.get("type", "string") == "string"):
                continue
            name = tool.name.lower()
            if any(word in name for word in ("explain", "analyze", "plan")):
                rank = 2
            elif any(word in name for word in ("execute", "run", "query", "sql")):
                rank = 0
            else:
                rank = 1
            candidates.append((rank, tool.name, param))
            break
    if not candidates:
        return None
    candidates.sort(key=lambda c: c[0])
    rank, name, param = candidates[0]
    if rank == 2:
        # Only explain/analyze-style tools accept SQL — none of them execute it.
        return None
    return name, param


def _parse_rows(text: str) -> list[dict[str, Any]]:
    """Best-effort extraction of result rows from a tool's text payload."""
    stripped = text.strip()
    if not stripped:
        return []
    payload = _loads(stripped)
    if payload is None:
        # Some servers emit one object per line.
        rows: list[dict[str, Any]] = []
        for line in stripped.splitlines():
            item = _loads(line)
            if not isinstance(item, dict):
                return []
            rows.append(item)
        return rows
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        for key in ("rows", "results", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict)]
        return [payload]
    return []


_DECIMAL_RE = re.compile(r"Decimal\('([^']*)'\)")
_UUID_RE = re.compile(r"UUID\('([^']*)'\)")
_DATETIME_RE = re.compile(r"datetime\.(date|datetime|time)\(([^)]*)\)")


def _replace_datetime(match: re.Match) -> str:
    """Render a datetime.* repr as a quoted ISO-ish string literal."""
    kind, args = match.group(1), match.group(2)
    try:
        parts = [int(p.strip()) for p in args.split(",")]
        if kind == "time":
            return "'" + ":".join(f"{p:02d}" for p in parts[:3]) + "'"
        date = f"{parts[0]:04d}-{parts[1]:02d}-{parts[2]:02d}"
        if len(parts) > 3:
            date += " " + ":".join(f"{p:02d}" for p in parts[3:6])
        return f"'{date}'"
    except (ValueError, IndexError):
        return "'" + args.replace("'", "") + "'"


def _loads(text: str) -> Any:
    """Parse JSON, falling back to Python literals (some servers emit repr).

    Postgres drivers repr non-literal types (Decimal('24.90'),
    datetime.date(2026, 1, 1), UUID('...')); normalize them to literals
    first so ast.literal_eval can handle the payload.
    """
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    normalized = _DECIMAL_RE.sub(r"\1", text)
    normalized = _UUID_RE.sub(r"'\1'", normalized)
    normalized = _DATETIME_RE.sub(_replace_datetime, normalized)
    try:
        return ast.literal_eval(normalized)
    except (ValueError, SyntaxError):
        return None


def _render_schema_summary(rows: list[dict[str, Any]], schema: str) -> str:
    tables: dict[str, list[str]] = {}
    for row in rows:
        table = str(row.get("table_name", ""))
        if not table:
            continue
        column = str(row.get("column_name", ""))
        parts = [column]
        if row.get("is_pk") in (True, "true", "t", 1):
            parts.append("[PK]")
        fk_ref = row.get("fk_ref")
        if fk_ref:
            parts.append(f"-> {fk_ref}")
        tables.setdefault(table, []).append(" ".join(parts))
    lines = [f"Schema '{schema}' — tables (columns, [PK] = primary key, -> = foreign key):"]
    for table, columns in tables.items():
        lines.append(f"- {schema}.{table}({', '.join(columns)})")
    return "\n".join(lines)
