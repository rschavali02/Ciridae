from app.config import Settings

def test_settings_reads_anthropic_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    settings = Settings()
    assert settings.anthropic_api_key == "sk-test"


def test_settings_reads_database_and_voyage(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@localhost/db")
    monkeypatch.setenv("VOYAGE_API_KEY", "pa-test")
    settings = Settings()
    assert settings.database_url == "postgresql+asyncpg://u:p@localhost/db"
    assert settings.voyage_api_key == "pa-test"
