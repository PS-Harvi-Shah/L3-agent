from sqlalchemy import select

from app.database.models import Product, Supplier
from app.repositories.base import BaseRepository
from app.schemas import ProductRead, SupplierRead


class MasterDataRepository(BaseRepository):
    def get_by_id(self, product_id: int) -> ProductRead | None:
        return self.get_by_product_id(product_id)

    def get_by_product_id(self, product_id: int) -> ProductRead | None:
        return self._execute(
            "master_data.get_by_product_id",
            lambda: self._to_schema(self.session.get(Product, product_id), ProductRead),
        )

    def get_by_name(self, product_name: str) -> list[ProductRead]:
        return self.get_by_product_name(product_name)

    def get_by_product_name(self, product_name: str) -> list[ProductRead]:
        return self._execute(
            "master_data.get_by_product_name",
            lambda: self._to_schema_list(
                self.session.scalars(
                    select(Product).where(Product.name.ilike(self._wildcard(product_name)))
                ).all(),
                ProductRead,
            ),
        )

    def get_by_sku(self, sku: str) -> ProductRead | None:
        return self._execute(
            "master_data.get_by_sku",
            lambda: self._to_schema(
                self.session.scalars(select(Product).where(Product.sku == sku)).first(),
                ProductRead,
            ),
        )

    def get_by_supplier_id(self, supplier_id: int) -> list[ProductRead]:
        return self._execute(
            "master_data.get_by_supplier_id",
            lambda: self._to_schema_list(
                self.session.scalars(
                    select(Product)
                    .join(Supplier, Supplier.id == Product.supplier_id)
                    .where(Supplier.id == supplier_id)
                ).all(),
                ProductRead,
            ),
        )

    def get_by_supplier_name(self, supplier_name: str) -> list[ProductRead]:
        return self._execute(
            "master_data.get_by_supplier_name",
            lambda: self._to_schema_list(
                self.session.scalars(
                    select(Product)
                    .join(Supplier, Supplier.id == Product.supplier_id)
                    .where(Supplier.name.ilike(self._wildcard(supplier_name)))
                ).all(),
                ProductRead,
            ),
        )

    def get_supplier_by_id(self, supplier_id: int) -> SupplierRead | None:
        return self._execute(
            "master_data.get_supplier_by_id",
            lambda: self._to_schema(
                self.session.scalars(select(Supplier).where(Supplier.id == supplier_id)).first(),
                SupplierRead,
            ),
        )

    def get_supplier_by_name(self, supplier_name: str) -> list[SupplierRead]:
        return self._execute(
            "master_data.get_supplier_by_name",
            lambda: self._to_schema_list(
                self.session.scalars(
                    select(Supplier).where(Supplier.name.ilike(self._wildcard(supplier_name)))
                ).all(),
                SupplierRead,
            ),
        )

    def get_by_part_number(self, part_number: str) -> ProductRead | None:
        return self._execute(
            "master_data.get_by_part_number",
            lambda: self._to_schema(
                self.session.scalars(
                    select(Product).where(Product.part_number == part_number)
                ).first(),
                ProductRead,
            ),
        )

    def get_supplier(self, product_id: int) -> SupplierRead | None:
        return self._execute(
            "master_data.get_supplier",
            lambda: self._to_schema(
                self.session.scalars(
                    select(Supplier)
                    .join(Product, Product.supplier_id == Supplier.id)
                    .where(Product.id == product_id)
                ).first(),
                SupplierRead,
            ),
        )

    def get_supplier_by_code(self, supplier_code: str) -> SupplierRead | None:
        return self._execute(
            "master_data.get_supplier_by_code",
            lambda: self._to_schema(
                self.session.scalars(select(Supplier).where(Supplier.code == supplier_code)).first(),
                SupplierRead,
            ),
        )

    get_by_code = get_supplier_by_code

    def get_products(self, supplier_id: int) -> list[ProductRead]:
        return self._execute(
            "master_data.get_products",
            lambda: self._to_schema_list(
                self.session.scalars(
                    select(Product)
                    .join(Supplier, Supplier.id == Product.supplier_id)
                    .where(Supplier.id == supplier_id)
                ).all(),
                ProductRead,
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
