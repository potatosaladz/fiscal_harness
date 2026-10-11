"""Environment-backed application settings."""

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from fiscal_astra.llm.settings import LlmAgentSettings


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
    llm_model_version: str | None = Field(default=None)
    llm_api_key: SecretStr | None = Field(default=None)
    llm_timeout_seconds: float = Field(default=60.0, gt=0, le=600)
    llm_max_attempts: int = Field(default=3, ge=1, le=10)
    llm_retry_backoff_seconds: float = Field(default=0.5, ge=0, le=60)
    llm_temperature: float = Field(default=0.0, ge=0, le=2)
    llm_max_tokens: int | None = Field(default=None, ge=1, le=1_000_000)
    llm_json_mode: bool = Field(default=True)
    llm_prompt_version: str = Field(default="v1", min_length=1, max_length=64)
    llm_agents: dict[str, LlmAgentSettings] = Field(default_factory=dict)

    def llm_default_agent_settings(self) -> LlmAgentSettings:
        """Return the process-wide LLM configuration shared by every agent."""

        return LlmAgentSettings(
            provider=self.llm_provider,
            base_url=self.llm_base_url,
            model=self.llm_model,
            model_version=self.llm_model_version,
            api_key=self.llm_api_key,
            timeout_seconds=self.llm_timeout_seconds,
            max_attempts=self.llm_max_attempts,
            retry_backoff_seconds=self.llm_retry_backoff_seconds,
            temperature=self.llm_temperature,
            max_tokens=self.llm_max_tokens,
            json_mode=self.llm_json_mode,
            prompt_version=self.llm_prompt_version,
        )

    def llm_agent_settings(self, agent_id: str) -> LlmAgentSettings:
        """Return the effective LLM configuration for one agent.

        ``ASTRA_LLM_AGENTS`` maps an agent id to the fields it overrides; every
        field it leaves unset falls back to the process-wide defaults, so a
        deployment can give the orchestrator, each policy sub-agent, and each
        simulation agent a different endpoint, model, or key.
        """

        defaults = self.llm_default_agent_settings()
        override = self.llm_agents.get(agent_id)
        if override is None:
            return defaults
        merged = defaults.model_dump()
        merged.update(override.model_dump(exclude_unset=True))
        return LlmAgentSettings(**merged)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return one cached settings instance for the application process."""

    return Settings()
