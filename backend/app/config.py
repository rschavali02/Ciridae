from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env")

    anthropic_api_key: str
    database_url: str
    voyage_api_key: str
    confidence_escalation_threshold: float = 0.7


settings = Settings()
