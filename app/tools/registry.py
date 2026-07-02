from typing import Any

from app.tools.base import BaseTool


class ToolRegistry:
    _instance: "ToolRegistry | None" = None
    _tools: dict[str, BaseTool] = {}

    def __new__(cls) -> "ToolRegistry":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._tools = {}
        return cls._instance

    def register(self, tool: BaseTool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> BaseTool | None:
        return self._tools.get(name)

    def list_tools(self) -> list[dict[str, Any]]:
        return [tool.metadata for tool in self._tools.values()]

    def get_names(self) -> list[str]:
        return list(self._tools.keys())

    def execute(self, name: str, params: dict[str, Any]):
        tool = self.get(name)
        if tool is None:
            raise ValueError(f"Unknown tool: {name}")
        return tool._measure(params)

    @classmethod
    def reset(cls) -> None:
        cls._instance = None
        cls._tools = {}
