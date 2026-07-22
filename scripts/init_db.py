"""Initialize PostgreSQL schema, seed data, and the read-only MCP role."""

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
    _ensure_readonly_role()
    print("Database initialized successfully.")


def _ensure_readonly_role() -> None:
    """Create/refresh the SELECT-only role the MCP server connects with.

    Statements are individually idempotent, so re-running is safe. Role
    creation can't live in init_db.sql: the naive `;` splitter breaks on
    DO-block bodies and Postgres has no CREATE ROLE IF NOT EXISTS.
    """
    role = settings.mcp_db_user
    if not role.isidentifier():
        raise ValueError(f"MCP_DB_USER '{role}' is not a valid role name")
    password = settings.mcp_db_password.replace("'", "''")

    with engine.connect() as connection:
        exists = connection.execute(
            text("SELECT 1 FROM pg_roles WHERE rolname = :role"), {"role": role}
        ).scalar()
        if not exists:
            connection.execute(text(f"CREATE ROLE {role} LOGIN PASSWORD '{password}'"))
        for statement in (
            f"GRANT CONNECT ON DATABASE {settings.postgres_db} TO {role}",
            f"GRANT USAGE ON SCHEMA enterprise_data TO {role}",
            f"GRANT SELECT ON ALL TABLES IN SCHEMA enterprise_data TO {role}",
            f"ALTER DEFAULT PRIVILEGES IN SCHEMA enterprise_data GRANT SELECT ON TABLES TO {role}",
            f"ALTER ROLE {role} SET default_transaction_read_only = on",
            f"REVOKE CREATE ON SCHEMA public FROM {role}",
        ):
            connection.execute(text(statement))
        connection.commit()
    print(f"Read-only role '{role}' ensured (SELECT-only on enterprise_data).")


def _split_statements(sql: str) -> list[str]:
    statements: list[str] = []
    for part in sql.split(";"):
        cleaned = part.strip()
        if cleaned:
            statements.append(cleaned)
    return statements


if __name__ == "__main__":
    run_init()
