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

    api_base_url: str = Field(default="http://localhost:8000", alias="API_BASE_URL")

    llm_provider: str = Field(default="ollama", alias="LLM_PROVIDER")
    llm_api_key: str | None = Field(default=None, alias="LLM_API_KEY")
    llm_base_url: str = Field(default="http://localhost:11434/v1", alias="LLM_BASE_URL")
    llm_model: str = Field(default="qwen3:8b", alias="LLM_MODEL")
    llm_timeout: int = Field(default=60, alias="LLM_TIMEOUT")

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


@lru_cache
def get_settings() -> Settings:
    return Settings()
