import logging
from collections.abc import Callable, Sequence
from typing import TypeVar

from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.repositories.exceptions import DataAccessError


ModelT = TypeVar("ModelT")
SchemaT = TypeVar("SchemaT", bound=BaseModel)


class BaseRepository:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.logger = logging.getLogger(self.__class__.__name__)

    def _execute(self, operation: str, query: Callable[[], ModelT]) -> ModelT:
        try:
            return query()
        except SQLAlchemyError as exc:
            self.logger.exception("Repository query failed", extra={"operation": operation})
            raise DataAccessError(f"{operation} failed") from exc

    @staticmethod
    def _to_schema(record: object | None, schema: type[SchemaT]) -> SchemaT | None:
        if record is None:
            return None
        return schema.model_validate(record)

    @staticmethod
    def _to_schema_list(records: Sequence[object], schema: type[SchemaT]) -> list[SchemaT]:
        return [schema.model_validate(record) for record in records]
