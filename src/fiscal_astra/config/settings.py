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
    broker_url: str = Field(default="redis://127.0.0.1:6379/0")
    simulation_queue_name: str = Field(default="simulations", min_length=1)
    orchestrator_queue_name: str = Field(default="orchestrator", min_length=1)
    simulation_max_attempts: int = Field(default=3, ge=1, le=20)
    simulation_retry_min_backoff_ms: int = Field(default=1_000, ge=0)
    simulation_retry_max_backoff_ms: int = Field(default=30_000, ge=0)
    s3_endpoint: str | None = Field(default=None)
    s3_region: str = Field(default="us-east-1")
    s3_access_key: str | None = Field(default=None)
    s3_secret_key: str | None = Field(default=None)
    s3_bucket_artifacts: str = Field(default="fiscal-astra-artifacts")
    s3_bucket_data: str = Field(default="fiscal-astra-data")
    s3_use_tls: bool = Field(default=True)
    artifact_root: str = Field(default="artifacts")
    data_root: str = Field(default="data")
    retrieval_url_allowlist: list[str] = Field(default_factory=list)
    retrieval_request_timeout_seconds: float = Field(default=15.0, gt=0)
    llm_provider: str = Field(default="mock")
    llm_base_url: str | None = Field(default=None)
    llm_model: str | None = Field(default=None)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return one cached settings instance for the application process."""

    return Settings()
