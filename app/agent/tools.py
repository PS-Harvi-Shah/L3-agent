"""Tools the agent can invoke.

The tools are no longer implemented here — they are discovered from the
external Postgres MCP server and passed straight through to the LLM. This
module only adapts between the MCP world and the agent loop:

- MCP tool listings -> OpenAI function-calling specs for the LLM
- LLM tool calls -> MCP ``call_tool`` requests
- MCP results -> ``ToolResult`` observations

The single decision this layer makes is a hard safety bound: SQL arguments
must be read-only (SELECT/WITH), enforced before anything reaches the server.
"""

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

from app.config import get_settings
from app.mcp_client import MCPClient, MCPClientError


logger = logging.getLogger("agent.tools")

_SQL_ARG_NAMES = ("sql", "query", "statement")
_READONLY_SQL = re.compile(r"^\s*(select|with)\b", re.IGNORECASE)
# Words that can make a SELECT/WITH statement write (data-modifying CTEs,
# stacked statements, DDL). Advisory layer only — the DB role is the
# guarantee — so false positives on string literals are acceptable.
_FORBIDDEN_SQL = re.compile(
    r"\b(insert|update|delete|drop|alter|create|truncate|grant|revoke|"
    r"copy|vacuum|merge|call|do|execute|set)\b",
    re.IGNORECASE,
)


@dataclass
class ToolResult:
    success: bool
    data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    record_count: int = 0
    execution_time_ms: float = 0.0


class ToolBelt:
    """The MCP server's tool set, handed to the agent for one query."""

    def __init__(self, mcp: MCPClient) -> None:
        self._mcp = mcp
        discovered = {tool.name: tool for tool in mcp.list_tools()}
        allowlist = {
            name.strip()
            for name in get_settings().mcp_tool_allowlist.split(",")
            if name.strip()
        }
        if allowlist:
            filtered = {n: t for n, t in discovered.items() if n in allowlist}
            # If the server exposes none of the allowlisted names, show all
            # tools rather than an empty catalog.
            self._tools = filtered or discovered
        else:
            self._tools = discovered

    def specs(self) -> list[dict[str, Any]]:
        """OpenAI/Ollama function-calling definitions of the MCP tools."""
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.input_schema,
                },
            }
            for tool in self._tools.values()
        ]

    def catalog(self) -> list[dict[str, Any]]:
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.input_schema,
            }
            for tool in self._tools.values()
        ]

    def execute(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        start = time.perf_counter()
        if name not in self._tools:
            known = ", ".join(self._tools)
            return ToolResult(
                success=False,
                error=f"Unknown tool '{name}'. Available tools: {known}",
            )

        blocked = _blocked_sql(arguments)
        if blocked:
            logger.warning(
                "Blocked non-read-only SQL", extra={"tool": name, "sql": blocked}
            )
            return ToolResult(
                success=False,
                error=(
                    "Rejected: only a single read-only SQL statement (SELECT or "
                    "WITH ... SELECT) is allowed — no data-modifying keywords, no "
                    "stacked statements. Rewrite it as one plain SELECT."
                ),
            )

        try:
            result = self._mcp.call_tool(name, arguments)
        except MCPClientError as exc:
            elapsed = round((time.perf_counter() - start) * 1000, 2)
            logger.exception("MCP tool call failed", extra={"tool": name})
            return ToolResult(success=False, error=str(exc), execution_time_ms=elapsed)

        elapsed = round((time.perf_counter() - start) * 1000, 2)
        if not result.success:
            logger.warning(
                "Tool returned an error",
                extra={"tool": name, "arguments": arguments, "error": result.error},
            )
            return ToolResult(
                success=False, error=result.error, execution_time_ms=elapsed
            )

        data: dict[str, Any] = {"count": result.count}
        if result.rows:
            data["rows"] = result.rows
        elif result.text.strip():
            data["result"] = result.text
        logger.info(
            "Tool executed",
            extra={
                "tool": name,
                "arguments": arguments,
                "record_count": result.count,
                "execution_time_ms": elapsed,
            },
        )
        return ToolResult(
            success=True,
            data=data,
            record_count=result.count,
            execution_time_ms=elapsed,
        )


def _blocked_sql(arguments: dict[str, Any]) -> str | None:
    """Return the offending statement if any SQL argument is not read-only.

    Rejects statements that (a) don't start with SELECT/WITH, (b) contain a
    data-modifying/DDL keyword anywhere (closes the writing-CTE hole, e.g.
    ``WITH x AS (INSERT ...) SELECT ...``), or (c) stack multiple statements
    with ``;``.
    """
    for key, value in arguments.items():
        if key.lower() in _SQL_ARG_NAMES and isinstance(value, str) and value.strip():
            statement = value.strip()
            if not _READONLY_SQL.match(statement):
                return value
            if _FORBIDDEN_SQL.search(statement):
                return value
            if ";" in statement.rstrip().rstrip(";"):
                return value
    return None
