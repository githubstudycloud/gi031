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

    # V3：summary 走预聚合表 (report_fact_ai_metrics_daily) 还是原始 ai_metric long-table。
    # True → 必须先调 /api/admin/preagg/refresh 灌好 preagg。
    use_preagg: bool = False


settings = Settings()
