import logging
import time
from typing import Any

from sqlalchemy.orm import Session

from app.repositories import MasterDataRepository
from app.services import RetrievalEngine
from app.tools import (
    MasterLookupTool,
    ProductLookupTool,
    ToolRegistry,
)


class MCPServer:
    def __init__(self, session: Session) -> None:
        self.logger = logging.getLogger(self.__class__.__name__)
        self._registry = ToolRegistry()
        self._init_tools(session)

    def _init_tools(self, session: Session) -> None:
        repo = MasterDataRepository(session)
        engine = RetrievalEngine(session)

        self._registry.register(ProductLookupTool(repo))
        self._registry.register(MasterLookupTool(engine))

        self.logger.info("MCP tools initialized", extra={"tool_count": len(self._registry.get_names())})

    @property
    def registry(self) -> ToolRegistry:
        return self._registry

    def list_tools(self) -> list[dict[str, Any]]:
        return self._registry.list_tools()

    def execute_tool(self, tool_name: str, params: dict[str, Any]) -> dict[str, Any]:
        self.logger.info("MCP execute", extra={"tool": tool_name, "params": params})
        start = time.perf_counter()
        result = self._registry.execute(tool_name, params)
        elapsed = (time.perf_counter() - start) * 1000

        payload = result.to_dict()
        payload["tool"] = tool_name
        payload["params"] = params
        payload["total_time_ms"] = round(elapsed, 2)

        self.logger.info(
            "MCP execution completed",
            extra={
                "tool": tool_name,
                "params": params,
                "success": result.success,
                "time_ms": round(elapsed, 2),
            },
        )
        return payload
