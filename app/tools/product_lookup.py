from typing import Any

from app.repositories import MasterDataRepository
from app.repositories.exceptions import DataAccessError
from app.tools.base import BaseTool, ToolResult


_SUPPORTED = ("product_id", "part_number", "product_name", "supplier_id", "supplier_name")


class ProductLookupTool(BaseTool):
    def __init__(self, repo: MasterDataRepository) -> None:
        super().__init__()
        self._repo = repo

    @property
    def name(self) -> str:
        return "product_lookup"

    @property
    def description(self) -> str:
        return (
            "Focused single-entity lookup. Returns only the records that directly match "
            "the given identifier (no cross-table consolidation). Use when you want just the "
            "product(s) or supplier(s) for one identifier."
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
            payload = self._lookup(identifier_type, value)
        except (ValueError, DataAccessError) as exc:
            return ToolResult(success=False, error=str(exc))

        if not payload["products"] and not payload["suppliers"]:
            return ToolResult(success=False, error="No matching records found", data=payload)
        return ToolResult(success=True, data=payload)

    def _lookup(self, identifier_type: str, value: str) -> dict[str, Any]:
        products: list[Any] = []
        suppliers: list[Any] = []

        match identifier_type:
            case "product_id":
                record = self._repo.get_by_product_id(int(value))
                products = [record] if record else []
            case "part_number":
                record = self._repo.get_by_part_number(value)
                products = [record] if record else []
            case "product_name":
                products = self._repo.get_by_product_name(value)
            case "supplier_id":
                supplier = self._repo.get_supplier_by_id(int(value))
                suppliers = [supplier] if supplier else []
            case "supplier_name":
                suppliers = self._repo.get_supplier_by_name(value)

        return {
            "identifier_type": identifier_type,
            "value": value,
            "products": [p.model_dump() for p in products if p is not None],
            "suppliers": [s.model_dump() for s in suppliers if s is not None],
        }
