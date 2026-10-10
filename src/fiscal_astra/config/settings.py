"""Environment-backed application settings."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration loaded from environment variables or ``.env``."""

    model_config = SettingsConfigDict(
        env_prefix="ASTRA_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = Field(default="Fiscal Astra")
    environment: str = Field(default="development")
    debug: bool = Field(default=False)
    host: str = Field(default="127.0.0.1")
    port: int = Field(default=8000, ge=1, le=65535)
    log_level: str = Field(default="INFO")
    database_url: str = Field(
        default="postgresql+psycopg://astra:change-me@127.0.0.1:5432/fiscal_astra"
    )
    artifact_root: str = Field(default="artifacts")
    llm_provider: str = Field(default="mock")
    llm_base_url: str | None = Field(default=None)
    llm_model: str | None = Field(default=None)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return one cached settings instance for the application process."""

    return Settings()
