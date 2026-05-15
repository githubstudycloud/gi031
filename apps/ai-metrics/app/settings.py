from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "sqlite:///./ai_metrics.db"
    cors_origins: str = "*"

    api_prefix: str = "/api"
    report_code: str = "ai_metrics"

    seed_days: int = 30
    seed_projects: int = 4
    seed_domains: int = 6


settings = Settings()
