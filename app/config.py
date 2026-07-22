from functools import lru_cache

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


load_dotenv()


class Settings(BaseSettings):
    """Application settings loaded from environment variables and .env."""

    app_name: str = Field(default="Master Data Discovery Agent", alias="APP_NAME")
    environment: str = Field(default="local", alias="ENVIRONMENT")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    postgres_host: str = Field(default="localhost", alias="POSTGRES_HOST")
    postgres_port: int = Field(default=5432, alias="POSTGRES_PORT")
    postgres_db: str = Field(default="master_data", alias="POSTGRES_DB")
    postgres_user: str = Field(default="postgres", alias="POSTGRES_USER")
    postgres_password: str = Field(default="postgres", alias="POSTGRES_PASSWORD")
    database_url: str | None = Field(default=None, alias="DATABASE_URL")

    api_base_url: str = Field(default="http://localhost:8123", alias="API_BASE_URL")

    # MCP server (the agent's data access — an external Postgres MCP server)
    mcp_transport: str = Field(default="stdio", alias="MCP_TRANSPORT")  # stdio | http
    mcp_server_command: str = Field(default="postgres-mcp", alias="MCP_SERVER_COMMAND")
    mcp_server_args: str = Field(default="--access-mode=restricted", alias="MCP_SERVER_ARGS")
    mcp_server_url: str = Field(default="http://localhost:8001/sse", alias="MCP_SERVER_URL")
    mcp_database_uri: str | None = Field(default=None, alias="MCP_DATABASE_URI")
    # Read-only DB role used by the MCP server (never the admin credentials).
    mcp_db_user: str = Field(default="mcp_readonly", alias="MCP_DB_USER")
    mcp_db_password: str = Field(default="mcp_readonly", alias="MCP_DB_PASSWORD")
    mcp_schema_in_prompt: bool = Field(default=True, alias="MCP_SCHEMA_IN_PROMPT")
    mcp_schema_name: str = Field(default="enterprise_data", alias="MCP_SCHEMA_NAME")
    # Comma-separated names of MCP tools shown to the LLM (empty = all).
    # Keeps irrelevant server tools (DBA analysis etc.) out of the prompt.
    mcp_tool_allowlist: str = Field(
        default="execute_sql,list_schemas,list_objects,get_object_details",
        alias="MCP_TOOL_ALLOWLIST",
    )

    # LLM (the agent's reasoning model)
    llm_provider: str = Field(default="ollama", alias="LLM_PROVIDER")
    llm_api_key: str | None = Field(default=None, alias="LLM_API_KEY")
    llm_base_url: str = Field(default="http://localhost:11434", alias="LLM_BASE_URL")
    llm_model: str = Field(default="qwen2.5:3b-instruct", alias="LLM_MODEL")
    llm_timeout: int = Field(default=300, alias="LLM_TIMEOUT")

    # Agent guardrails (bound the loop; they never make decisions)
    agent_max_steps: int = Field(default=8, alias="AGENT_MAX_STEPS")
    agent_deadline_seconds: float = Field(default=180.0, alias="AGENT_DEADLINE_SECONDS")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    @property
    def sqlalchemy_database_url(self) -> str:
        if self.database_url:
            return self.database_url

        return (
            "postgresql+psycopg2://"
            f"{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def resolved_mcp_database_uri(self) -> str:
        """The DATABASE_URI handed to the MCP server subprocess.

        Uses the dedicated read-only role — the MCP server must never
        connect with the admin credentials.
        """
        if self.mcp_database_uri:
            return self.mcp_database_uri
        return (
            "postgresql://"
            f"{self.mcp_db_user}:{self.mcp_db_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
