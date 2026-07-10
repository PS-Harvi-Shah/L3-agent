from sqlalchemy import func, select

from app.database.models import Product, Supplier
from app.repositories.base import BaseRepository
from app.schemas import ProductRead, SupplierRead


class MasterDataRepository(BaseRepository):
    """Read-only access to the enterprise_data schema.

    Every method maps to exactly one SQL query and returns validated
    Pydantic records. No interpretation of user intent happens here —
    the agent decides what to look up.
    """

    def get_product_by_id(self, product_id: int) -> ProductRead | None:
        return self._execute(
            "get_product_by_id",
            lambda: self._to_schema(self.session.get(Product, product_id), ProductRead),
        )

    def find_products_by_name(self, product_name: str, limit: int = 25) -> list[ProductRead]:
        return self._execute(
            "find_products_by_name",
            lambda: self._to_schema_list(
                self.session.scalars(
                    select(Product)
                    .where(Product.product_name.ilike(self._wildcard(product_name)))
                    .order_by(Product.product_id)
                    .limit(limit)
                ).all(),
                ProductRead,
            ),
        )

    def find_products_by_part_number(self, part_number: str, limit: int = 25) -> list[ProductRead]:
        return self._execute(
            "find_products_by_part_number",
            lambda: self._to_schema_list(
                self.session.scalars(
                    select(Product)
                    .where(func.lower(Product.part_number) == part_number.strip().lower())
                    .order_by(Product.product_id)
                    .limit(limit)
                ).all(),
                ProductRead,
            ),
        )

    def get_products_by_supplier(self, supplier_id: int, limit: int = 100) -> list[ProductRead]:
        return self._execute(
            "get_products_by_supplier",
            lambda: self._to_schema_list(
                self.session.scalars(
                    select(Product)
                    .where(Product.supplier_id == supplier_id)
                    .order_by(Product.product_id)
                    .limit(limit)
                ).all(),
                ProductRead,
            ),
        )

    def get_supplier_by_id(self, supplier_id: int) -> SupplierRead | None:
        return self._execute(
            "get_supplier_by_id",
            lambda: self._to_schema(self.session.get(Supplier, supplier_id), SupplierRead),
        )

    def find_suppliers_by_name(self, supplier_name: str, limit: int = 25) -> list[SupplierRead]:
        return self._execute(
            "find_suppliers_by_name",
            lambda: self._to_schema_list(
                self.session.scalars(
                    select(Supplier)
                    .where(Supplier.supplier_name.ilike(self._wildcard(supplier_name)))
                    .order_by(Supplier.supplier_id)
                    .limit(limit)
                ).all(),
                SupplierRead,
            ),
        )

    @staticmethod
    def _wildcard(value: str) -> str:
        stripped = value.strip()
        if not stripped:
            return "%"
        if "%" in stripped or "_" in stripped:
            return stripped
        return f"%{stripped}%"
