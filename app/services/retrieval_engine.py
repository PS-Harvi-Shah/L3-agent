import logging
import re
from typing import Any

from sqlalchemy.orm import Session

from app.repositories import MasterDataRepository
from app.repositories.exceptions import DataAccessError
from app.schemas import IdentifierMatch


_ENTITY_BY_IDENTIFIER: dict[str, str] = {
    "product_id": "product",
    "product_name": "product",
    "part_number": "product",
    "supplier_id": "supplier",
    "supplier_name": "supplier",
}


class RetrievalEngine:
    """Deterministic consolidation engine over master data.

    ``retrieve`` is the structured entry point used by the agent's tools: the
    agent decides the identifier type and this engine simply gathers the entity
    plus all records related to it across tables. ``retrieve_from_query`` is a
    convenience wrapper for the non-agentic REST endpoint that first guesses the
    identifier type; the agent never uses it.
    """

    _PREFIX_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
        (re.compile(r"^(?:product|product_id|pid)[:#\s-]+(.+)$", re.IGNORECASE), "product_id"),
        (re.compile(r"^(?:supplier|supplier_id|sid)[:#\s-]+(.+)$", re.IGNORECASE), "supplier_id"),
        (re.compile(r"^(?:part|part_number|part no|part_no)[:#\s-]+(.+)$", re.IGNORECASE), "part_number"),
    )

    def __init__(self, session: Session) -> None:
        self.logger = logging.getLogger(self.__class__.__name__)
        self.repo = MasterDataRepository(session)

    def retrieve(self, identifier_type: str, value: str) -> dict[str, Any]:
        identifier_type = identifier_type.strip().lower()
        value = value.strip()
        entity_type = _ENTITY_BY_IDENTIFIER.get(identifier_type)
        if entity_type is None:
            raise ValueError(
                f"Unsupported identifier_type '{identifier_type}'. "
                f"Supported: {', '.join(_ENTITY_BY_IDENTIFIER)}"
            )
        if not value:
            raise ValueError("Query value cannot be empty")

        self.logger.info(
            "Consolidating records",
            extra={"entity_type": entity_type, "identifier_type": identifier_type},
        )
        payload = self._get_primary_entity(entity_type, identifier_type, value)

        if payload is None:
            return {
                "query": value,
                "entity_type": None,
                "identifier_type": identifier_type,
                "matched_identifier": None,
                "primary": None,
                "products": [],
                "suppliers": [],
                "raw_records": {},
            }

        return {
            "query": value,
            "entity_type": entity_type,
            "identifier_type": identifier_type,
            "matched_identifier": value,
            "primary": payload.get("primary"),
            "products": payload.get("products", []),
            "suppliers": payload.get("suppliers", []),
            "raw_records": payload,
        }

    def retrieve_from_query(self, query: str) -> dict[str, Any]:
        normalized_query = query.strip()
        if not normalized_query:
            raise ValueError("Query cannot be empty")

        for candidate in self._detect_identifier_candidates(normalized_query):
            result = self.retrieve(candidate.identifier_type, candidate.value)
            if result["entity_type"] is not None:
                return result

        return {
            "query": normalized_query,
            "entity_type": None,
            "identifier_type": None,
            "matched_identifier": None,
            "primary": None,
            "products": [],
            "suppliers": [],
            "raw_records": {},
        }

    def _detect_identifier_candidates(self, query: str) -> list[IdentifierMatch]:
        for pattern, identifier_type in self._PREFIX_PATTERNS:
            match = pattern.match(query)
            if match:
                value = match.group(1).strip()
                return [
                    IdentifierMatch(
                        entity_type=_ENTITY_BY_IDENTIFIER[identifier_type],
                        identifier_type=identifier_type,
                        value=value,
                    )
                ]

        if query.isdigit():
            return [
                IdentifierMatch(entity_type="product", identifier_type="product_id", value=query),
                IdentifierMatch(entity_type="supplier", identifier_type="supplier_id", value=query),
            ]

        if re.fullmatch(r"[A-Z0-9]{1,20}[-_][A-Z0-9][A-Z0-9-_]*", query, re.IGNORECASE):
            return [IdentifierMatch(entity_type="product", identifier_type="part_number", value=query)]

        return [
            IdentifierMatch(entity_type="product", identifier_type="product_name", value=query),
            IdentifierMatch(entity_type="supplier", identifier_type="supplier_name", value=query),
        ]

    def _get_primary_entity(
        self, entity_type: str, identifier_type: str, value: str
    ) -> dict[str, Any] | None:
        try:
            match (entity_type, identifier_type):
                case ("product", "product_id"):
                    record = self.repo.get_by_product_id(int(value))
                    return self._build_product_payload(record, identifier_type, value) if record else None
                case ("product", "product_name"):
                    records = self.repo.get_by_product_name(value)
                    return self._build_product_list_payload(records, identifier_type, value) if records else None
                case ("product", "part_number"):
                    record = self.repo.get_by_part_number(value)
                    return self._build_product_payload(record, identifier_type, value) if record else None
                case ("supplier", "supplier_id"):
                    record = self.repo.get_supplier_by_id(int(value))
                    return self._build_supplier_payload(record, identifier_type, value) if record else None
                case ("supplier", "supplier_name"):
                    records = self.repo.get_supplier_by_name(value)
                    return self._build_supplier_list_payload(records, identifier_type, value) if records else None
                case _:
                    return None
        except ValueError:
            return None
        except DataAccessError:
            raise

    def _build_product_payload(self, product: Any, identifier_type: str, value: str) -> dict[str, Any]:
        suppliers: list[dict[str, Any]] = []
        supplier = self.repo.get_supplier(product.id)
        if supplier is not None:
            suppliers.append(supplier.model_dump())
        return {
            "entity_type": "product",
            "identifier_type": identifier_type,
            "matched_identifier": value,
            "primary": product.model_dump(),
            "products": [product.model_dump()],
            "suppliers": suppliers,
        }

    def _build_product_list_payload(self, products: list[Any], identifier_type: str, value: str) -> dict[str, Any]:
        product_payloads = [product.model_dump() for product in products]
        supplier_payloads: list[dict[str, Any]] = []
        for product in products:
            supplier = self.repo.get_supplier(product.id)
            if supplier is not None and not self._contains_id(supplier_payloads, supplier.id):
                supplier_payloads.append(supplier.model_dump())
        return {
            "entity_type": "product",
            "identifier_type": identifier_type,
            "matched_identifier": value,
            "primary": product_payloads[0],
            "products": product_payloads,
            "suppliers": supplier_payloads,
        }

    def _build_supplier_payload(self, supplier: Any, identifier_type: str, value: str) -> dict[str, Any]:
        supplier_payload = supplier.model_dump()
        products = self.repo.get_products(supplier.id)
        return {
            "entity_type": "supplier",
            "identifier_type": identifier_type,
            "matched_identifier": value,
            "primary": supplier_payload,
            "products": [product.model_dump() for product in products],
            "suppliers": [supplier_payload],
        }

    def _build_supplier_list_payload(
        self,
        suppliers: list[Any],
        identifier_type: str,
        value: str,
    ) -> dict[str, Any]:
        supplier_payloads = [supplier.model_dump() for supplier in suppliers]
        products: list[dict[str, Any]] = []
        for supplier in suppliers:
            for product in self.repo.get_products(supplier.id):
                if not self._contains_id(products, product.id):
                    products.append(product.model_dump())
        return {
            "entity_type": "supplier",
            "identifier_type": identifier_type,
            "matched_identifier": value,
            "primary": supplier_payloads[0],
            "products": products,
            "suppliers": supplier_payloads,
        }

    @staticmethod
    def _contains_id(records: list[dict[str, Any]], record_id: int) -> bool:
        return any(record.get("id") == record_id for record in records)
