from typing import Any

from app.repositories.exceptions import DataAccessError
from app.services import RetrievalEngine
from app.tools.base import BaseTool, ToolResult


_SUPPORTED = ("product_id", "part_number", "product_name", "supplier_id", "supplier_name")


class MasterLookupTool(BaseTool):
    def __init__(self, engine: RetrievalEngine) -> None:
        super().__init__()
        self._engine = engine

    @property
    def name(self) -> str:
        return "master_lookup"

    @property
    def description(self) -> str:
        return (
            "Full hierarchical consolidation. Given one identifier, returns the matched entity "
            "together with every related record across tables (a product with its supplier, or a "
            "supplier with all its products). Use when the goal needs a complete, joined view."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "identifier_type": {
                    "type": "string",
                    "enum": list(_SUPPORTED),
                    "description": "Which kind of identifier 'value' is.",
                },
                "value": {"type": "string", "description": "The identifier value to search for."},
            },
            "required": ["identifier_type", "value"],
        }

    def execute(self, params: dict[str, Any]) -> ToolResult:
        identifier_type = str(params.get("identifier_type", "")).strip().lower()
        value = str(params.get("value", "")).strip()

        if identifier_type not in _SUPPORTED:
            return ToolResult(
                success=False,
                error=f"Unsupported identifier_type '{identifier_type}'. Supported: {', '.join(_SUPPORTED)}",
            )
        if not value:
            return ToolResult(success=False, error="'value' cannot be empty")

        try:
            response = self._engine.retrieve(identifier_type, value)
        except (ValueError, DataAccessError) as exc:
            return ToolResult(success=False, error=str(exc))

        if response.get("entity_type") is None:
            return ToolResult(success=False, error="No matching records found", data=response)
        return ToolResult(success=True, data=response)
