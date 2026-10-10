from fiscal_astra.config.settings import Settings


def test_settings_use_safe_local_defaults() -> None:
    settings = Settings(_env_file=None)

    assert settings.environment == "development"
    assert settings.llm_provider == "mock"
    assert settings.port == 8000
