from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env")

    anthropic_api_key: str
    database_url: str
    voyage_api_key: str
    confidence_escalation_threshold: float = 0.7

    # A second, throwaway database for the eval harness and the test suite
    eval_database_url: str | None = None


settings = Settings()


def require_eval_database_url() -> str:
    """The eval/test database URL, or a refusal to run without one.

    Deliberately does not fall back to `database_url`: that would put the
    wipe-everything code back on the development database at exactly the moment
    the setting is missing.
    """
    if not settings.eval_database_url:
        raise RuntimeError(
            "EVAL_DATABASE_URL is not set. The eval harness and the test suite "
            "empty every table before each trial, so they refuse to run against "
            "the application database. See backend/README-eval-db.md for the "
            "one-time setup."
        )
    return settings.eval_database_url
