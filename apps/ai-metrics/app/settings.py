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

    # V4：generation service 的调度器。空 cron 字符串 = 关闭对应任务
    # cron 5 字段格式 minute hour day month day_of_week (与 unix cron 一致)
    sim_cron: str = "0 */2 * * *"       # 每 2 小时拉一次模拟数据
    preagg_cron: str = "10 */2 * * *"   # 比模拟晚 10 分钟刷预聚合
    sim_days_back: int = 1               # 每次模拟最近 N 天
    sim_runs_per_call: int = 1           # 每次跑几个版本


settings = Settings()
