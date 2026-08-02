from app.config import Settings

def test_settings_reads_anthropic_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    settings = Settings()
    assert settings.anthropic_api_key == "sk-test"
