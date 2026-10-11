from fiscal_astra.config.settings import Settings
from fiscal_astra.llm.agents import ORCHESTRATOR_AGENT_ID


def test_settings_use_safe_local_defaults() -> None:
    settings = Settings(_env_file=None)

    assert settings.environment == "development"
    assert settings.llm_provider == "mock"
    assert settings.port == 8000


def test_llm_settings_are_loaded_from_environment_with_agent_override(monkeypatch) -> None:
    monkeypatch.setenv("ASTRA_LLM_PROVIDER", "openai_compatible")
    monkeypatch.setenv("ASTRA_LLM_BASE_URL", "http://planner.test/v1")
    monkeypatch.setenv("ASTRA_LLM_MODEL", "planner")
    monkeypatch.setenv("ASTRA_LLM_MODEL_VERSION", "planner-2")
    monkeypatch.setenv("ASTRA_LLM_TIMEOUT_SECONDS", "12")
    monkeypatch.setenv("ASTRA_LLM_MAX_ATTEMPTS", "2")
    monkeypatch.setenv(
        "ASTRA_LLM_AGENTS",
        '{"orchestrator":{"base_url":"http://orchestrator.test/v1",'
        '"model":"orchestrator-model","model_version":"orchestrator-3"}}',
    )

    settings = Settings(_env_file=None)
    agent = settings.llm_agent_settings(ORCHESTRATOR_AGENT_ID)

    assert agent.provider == "openai_compatible"
    assert agent.base_url == "http://orchestrator.test/v1"
    assert agent.model == "orchestrator-model"
    assert agent.model_version == "orchestrator-3"
    assert agent.timeout_seconds == 12
    assert agent.max_attempts == 2
