from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


SCHEMA_NAME = "enterprise_data"


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Supplier(Base, TimestampMixin):
    __tablename__ = "suppliers"
    __table_args__ = {"schema": SCHEMA_NAME}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    contact_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str | None] = mapped_column(String(100), nullable=True)

    products: Mapped[list["Product"]] = relationship(back_populates="supplier", cascade="none")


class Product(Base, TimestampMixin):
    __tablename__ = "products"
    __table_args__ = {"schema": SCHEMA_NAME}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    supplier_id: Mapped[int | None] = mapped_column(
        ForeignKey(f"{SCHEMA_NAME}.suppliers.id"),
        nullable=True,
    )
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sku: Mapped[str | None] = mapped_column(String(100), nullable=True)
    part_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    category: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str | None] = mapped_column(String(100), nullable=True)

    supplier: Mapped[Supplier | None] = relationship(back_populates="products")
