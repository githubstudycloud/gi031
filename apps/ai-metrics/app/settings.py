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

    # V4：默认走预聚合表 (report_fact_ai_metrics_daily)。
    # generation service 的调度器会按 preagg_cron 维护这张表。
    use_preagg: bool = False

    # V4：generation service 的调度器。空 cron 字符串 = 关闭对应任务
    # cron 5 字段格式 minute hour day month day_of_week (与 unix cron 一致)
    sim_cron: str = "0 */2 * * *"       # 每 2 小时拉一次模拟数据
    preagg_cron: str = "10 */2 * * *"   # 比模拟晚 10 分钟刷预聚合
    sim_days_back: int = 1               # 每次模拟最近 N 天
    sim_runs_per_call: int = 1           # 每次跑几个版本

    # V4 admin 鉴权：空字符串 = dev 模式无鉴权（启动时 warn）
    # 设了之后，所有 /api/admin/* 与 /api/metrics/ingest* 必须带 X-Admin-Token
    admin_token: str = ""

    # V4 admin UI 跨服务跳转。HTML 通过 /api/_meta 拿到这些 URL；
    # 留空 → 前端 fallback 到同 origin / 8002→8001 启发式
    query_url: str = ""        # 给 admin UI 跳回 /ui /vue /react 用
    generation_url: str = ""   # 留作前端预留


settings = Settings()
