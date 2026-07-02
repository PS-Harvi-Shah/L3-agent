import logging
import time
from abc import ABC, abstractmethod
from typing import Any


class ToolResult:
    def __init__(
        self,
        success: bool,
        data: Any = None,
        error: str | None = None,
        execution_time_ms: float = 0.0,
    ) -> None:
        self.success = success
        self.data = data
        self.error = error
        self.execution_time_ms = execution_time_ms

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "data": self.data,
            "error": self.error,
            "execution_time_ms": round(self.execution_time_ms, 2),
        }


class BaseTool(ABC):
    """A deterministic execution unit.

    Tools are the agent's hands, not its brain. They never guess intent or
    infer which identifier the user supplied — the agent decides that and
    passes explicit, structured arguments that conform to ``parameters``.
    """

    def __init__(self) -> None:
        self.logger = logging.getLogger(self.__class__.__name__)

    @property
    @abstractmethod
    def name(self) -> str:
        ...

    @property
    @abstractmethod
    def description(self) -> str:
        ...

    @property
    @abstractmethod
    def parameters(self) -> dict[str, Any]:
        """JSON-schema description of the arguments the agent must supply."""

    @property
    def metadata(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }

    @abstractmethod
    def execute(self, params: dict[str, Any]) -> ToolResult:
        """Run the tool against validated, agent-supplied arguments."""

    def _measure(self, params: dict[str, Any]) -> ToolResult:
        start = time.perf_counter()
        try:
            result = self.execute(params)
            elapsed = (time.perf_counter() - start) * 1000
            result.execution_time_ms = elapsed
            self.logger.info(
                "Tool executed",
                extra={
                    "tool": self.name,
                    "params": params,
                    "success": result.success,
                    "execution_time_ms": round(elapsed, 2),
                },
            )
            return result
        except Exception as exc:
            elapsed = (time.perf_counter() - start) * 1000
            self.logger.exception("Tool execution failed", extra={"tool": self.name, "params": params})
            return ToolResult(success=False, error=str(exc), execution_time_ms=elapsed)
