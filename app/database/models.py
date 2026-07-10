from sqlalchemy import BigInteger, ForeignKey, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


SCHEMA_NAME = "enterprise_data"


class Base(DeclarativeBase):
    pass


class Supplier(Base):
    __tablename__ = "suppliers"
    __table_args__ = {"schema": SCHEMA_NAME}

    supplier_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    supplier_name: Mapped[str] = mapped_column(String(500), nullable=False)

    products: Mapped[list["Product"]] = relationship(back_populates="supplier")


class Product(Base):
    __tablename__ = "products"
    __table_args__ = {"schema": SCHEMA_NAME}

    product_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    product_name: Mapped[str] = mapped_column(String(500), nullable=False)
    supplier_id: Mapped[int] = mapped_column(
        ForeignKey(f"{SCHEMA_NAME}.suppliers.supplier_id"), nullable=False
    )
    part_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    language: Mapped[str | None] = mapped_column(String(50), nullable=True)
    country: Mapped[str | None] = mapped_column(String(100), nullable=True)

    supplier: Mapped[Supplier] = relationship(back_populates="products")
