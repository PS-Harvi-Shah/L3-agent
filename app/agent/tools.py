"""Tools the agent can invoke.

Tools are the agent's hands, not its brain: each one is a deterministic,
single-purpose lookup against the master-data repository. They never guess
what the user meant — the agent supplies explicit arguments.
"""

import logging
import time
from dataclasses import dataclass
from typing import Any, Callable

from pydantic import BaseModel

from app.repositories.exceptions import DataAccessError
from app.repositories.master_data import MasterDataRepository


logger = logging.getLogger("agent.tools")


@dataclass
class ToolResult:
    success: bool
    data: dict[str, Any]
    error: str | None = None
    record_count: int = 0
    execution_time_ms: float = 0.0


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[[dict[str, Any]], dict[str, Any]]

    @property
    def spec(self) -> dict[str, Any]:
        """OpenAI/Ollama function-calling tool definition."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolBelt:
    """The executable tool set handed to the agent for one query."""

    def __init__(self, repo: MasterDataRepository) -> None:
        self._repo = repo
        self._tools: dict[str, Tool] = {t.name: t for t in self._build()}

    def specs(self) -> list[dict[str, Any]]:
        return [tool.spec for tool in self._tools.values()]

    def catalog(self) -> list[dict[str, Any]]:
        return [
            {"name": t.name, "description": t.description, "parameters": t.parameters}
            for t in self._tools.values()
        ]

    def execute(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        tool = self._tools.get(name)
        start = time.perf_counter()
        if tool is None:
            known = ", ".join(self._tools)
            return ToolResult(
                success=False,
                data={},
                error=f"Unknown tool '{name}'. Available tools: {known}",
            )
        try:
            data = tool.handler(arguments)
            elapsed = round((time.perf_counter() - start) * 1000, 2)
            count = int(data.get("count", 0))
            logger.info(
                "Tool executed",
                extra={
                    "tool": name,
                    "arguments": arguments,
                    "record_count": count,
                    "execution_time_ms": elapsed,
                },
            )
            return ToolResult(
                success=True, data=data, record_count=count, execution_time_ms=elapsed
            )
        except (ValueError, TypeError) as exc:
            elapsed = round((time.perf_counter() - start) * 1000, 2)
            logger.warning(
                "Tool rejected arguments",
                extra={"tool": name, "arguments": arguments, "error": str(exc)},
            )
            return ToolResult(
                success=False, data={}, error=f"Invalid arguments: {exc}", execution_time_ms=elapsed
            )
        except DataAccessError as exc:
            elapsed = round((time.perf_counter() - start) * 1000, 2)
            logger.exception("Tool database error", extra={"tool": name, "arguments": arguments})
            return ToolResult(
                success=False, data={}, error=f"Database error: {exc}", execution_time_ms=elapsed
            )

    def _build(self) -> list[Tool]:
        return [
            Tool(
                name="search_master_data",
                description=(
                    "Search the master data by ONE identifier interpretation. "
                    "identifier_type says how to interpret the value: 'product_id' (exact "
                    "numeric id), 'part_number' (exact code, case-insensitive), "
                    "'product_name' (partial, case-insensitive), 'supplier_id' (exact "
                    "numeric id), or 'supplier_name' (partial, case-insensitive). "
                    "Product matches are returned WITH their supplier details already "
                    "included — no further lookup is needed for a product's supplier."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "identifier_type": {
                            "type": "string",
                            "enum": [
                                "product_id",
                                "part_number",
                                "product_name",
                                "supplier_id",
                                "supplier_name",
                            ],
                        },
                        "value": {"type": "string", "description": "The identifier value."},
                    },
                    "required": ["identifier_type", "value"],
                },
                handler=self._search_master_data,
            ),
            Tool(
                name="get_products_of_supplier",
                description=(
                    "List all products supplied by a known supplier. Use after finding a "
                    "supplier to complete its master data."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "supplier_id": {
                            "type": "integer",
                            "description": "The supplier_id of the found supplier.",
                        }
                    },
                    "required": ["supplier_id"],
                },
                handler=self._get_supplier_products,
            ),
        ]

    # --- handlers -----------------------------------------------------------

    def _search_master_data(self, args: dict[str, Any]) -> dict[str, Any]:
        identifier_type = str(args.get("identifier_type", "")).strip().lower()
        value = str(args.get("value", "")).strip()
        if not value:
            raise ValueError("'value' must not be empty")

        if identifier_type == "product_id":
            record = self._repo.get_product_by_id(_as_int(value, "product_id"))
            products = [record] if record else []
        elif identifier_type == "part_number":
            products = self._repo.find_products_by_part_number(value)
        elif identifier_type == "product_name":
            products = self._repo.find_products_by_name(value)
        elif identifier_type == "supplier_id":
            record = self._repo.get_supplier_by_id(_as_int(value, "supplier_id"))
            suppliers = [record] if record else []
            return {"count": len(suppliers), "suppliers": _dump(suppliers)}
        elif identifier_type == "supplier_name":
            suppliers = self._repo.find_suppliers_by_name(value)
            return {"count": len(suppliers), "suppliers": _dump(suppliers)}
        else:
            raise ValueError(
                f"identifier_type must be one of product_id, part_number, product_name, "
                f"supplier_id, supplier_name — got '{identifier_type}'."
            )
        return self._with_suppliers(_dump(products))

    def _with_suppliers(self, products: list[dict[str, Any]]) -> dict[str, Any]:
        """Join each product with its supplier record (deterministic data
        consolidation — the agent still decides what to search and when)."""
        suppliers: dict[int, dict[str, Any]] = {}
        for product in products:
            supplier_id = product.get("supplier_id")
            if supplier_id is None or supplier_id in suppliers:
                continue
            record = self._repo.get_supplier_by_id(supplier_id)
            if record is not None:
                suppliers[supplier_id] = record.model_dump()
        for product in products:
            supplier = suppliers.get(product.get("supplier_id"))
            product["supplier_name"] = supplier["supplier_name"] if supplier else None
        return {
            "count": len(products),
            "products": products,
            "suppliers": list(suppliers.values()),
        }

    def _get_supplier_products(self, args: dict[str, Any]) -> dict[str, Any]:
        supplier_id = _as_int(args.get("supplier_id"), "supplier_id")
        products = self._repo.get_products_by_supplier(supplier_id)
        return self._with_suppliers(_dump(products))


def _as_int(value: Any, name: str) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        raise ValueError(f"'{name}' must be an integer, got '{value}'") from None


def _dump(records: list[BaseModel | None]) -> list[dict[str, Any]]:
    return [r.model_dump() for r in records if r is not None]
