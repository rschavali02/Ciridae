from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env")

    anthropic_api_key: str
    database_url: str
    voyage_api_key: str
    confidence_escalation_threshold: float = 0.7

    # A second, throwaway database for the eval harness and the test suite.
    #
    # Both of those empty every table before each trial/test and rely on an
    # outer transaction to put the rows back. That works, but it makes "does
    # running the tests destroy my data" a question you can only answer by
    # reading transaction semantics -- and one refactor away from the answer
    # silently becoming yes. Pointing them at a different database makes it
    # unanswerable by construction.
    #
    # Optional rather than required: `Settings()` is constructed at import of
    # this module, which `main.py` imports, so a missing value here would take
    # the whole application down rather than just the thing that needs it. The
    # two consumers raise for themselves instead -- see `app/eval/harness.py`.
    eval_database_url: str | None = None


settings = Settings()


def require_eval_database_url() -> str:
    """The eval/test database URL, or a refusal to run without one.

    Deliberately does not fall back to `database_url`. A fallback would put the
    wipe-everything code back on the development database at exactly the moment
    the setting is missing -- which is the failure this split exists to prevent,
    reintroduced with an extra step. Failing loudly costs one env var; falling
    back costs the demo data.
    """
    if not settings.eval_database_url:
        raise RuntimeError(
            "EVAL_DATABASE_URL is not set. The eval harness and the test suite "
            "empty every table before each trial, so they refuse to run against "
            "the application database. See backend/README-eval-db.md for the "
            "one-time setup."
        )
    return settings.eval_database_url
