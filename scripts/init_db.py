"""Initialize PostgreSQL schema and seed data for the master data agent."""

from pathlib import Path

from sqlalchemy import text

from app.database.connection import engine, settings


def run_init() -> None:
    sql_path = Path(__file__).resolve().parent / "init_db.sql"
    sql = sql_path.read_text(encoding="utf-8")

    print(f"Connecting to {settings.postgres_host}:{settings.postgres_port}/{settings.postgres_db}")
    with engine.connect() as connection:
        for statement in _split_statements(sql):
            connection.execute(text(statement))
        connection.commit()
    print("Database initialized successfully.")


def _split_statements(sql: str) -> list[str]:
    statements: list[str] = []
    for part in sql.split(";"):
        cleaned = part.strip()
        if cleaned:
            statements.append(cleaned)
    return statements


if __name__ == "__main__":
    run_init()
