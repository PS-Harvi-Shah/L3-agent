from sqlalchemy.orm import Session

from app.repositories import MasterDataRepository
from app.schemas import LookupResult, ProductRead, SupplierRead


class DataAccessService:
    """Lightweight data retrieval service over master data."""

    def __init__(self, session: Session) -> None:
        self.repo = MasterDataRepository(session)

    def lookup(self, entity_type: str, identifier_type: str, value: str) -> LookupResult:
        normalized_entity = entity_type.strip().lower().replace("-", "_")
        normalized_identifier = identifier_type.strip().lower().replace("-", "_")

        results: list[ProductRead | SupplierRead] = []

        match normalized_entity:
            case "product" | "products":
                results = self._lookup_product(normalized_identifier, value)
                entity = "product"
            case "supplier" | "suppliers":
                results = self._lookup_supplier(normalized_identifier, value)
                entity = "supplier"
            case _:
                raise ValueError(f"Unsupported entity type: {entity_type}")

        return LookupResult(entity_type=entity, count=len(results), results=results)

    def _lookup_product(self, identifier_type: str, value: str) -> list[ProductRead]:
        match identifier_type:
            case "id" | "product_id":
                record = self.repo.get_by_product_id(int(value))
                return [record] if record else []
            case "name" | "product_name":
                return self.repo.get_by_product_name(value)
            case "part_number":
                record = self.repo.get_by_part_number(value)
                return [record] if record else []
            case "supplier_id":
                return self.repo.get_by_supplier_id(int(value))
            case "supplier_name":
                return self.repo.get_by_supplier_name(value)
            case _:
                raise ValueError(f"Unsupported product identifier: {identifier_type}")

    def _lookup_supplier(self, identifier_type: str, value: str) -> list[SupplierRead]:
        match identifier_type:
            case "id" | "supplier_id":
                record = self.repo.get_supplier_by_id(int(value))
                return [record] if record else []
            case "name" | "supplier_name":
                return self.repo.get_supplier_by_name(value)
            case "code" | "supplier_code":
                record = self.repo.get_by_code(value)
                return [record] if record else []
            case _:
                raise ValueError(f"Unsupported supplier identifier: {identifier_type}")
