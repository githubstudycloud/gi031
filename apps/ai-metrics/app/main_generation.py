"""**报表生成服务** (Generation Service)。

与 `app.main_query` (只读) 配套；本服务负责：
1. 数据写入 (ingest, simulate)
2. Admin 元数据 CRUD (project / domain / project_domain / metric_def)
3. 预聚合刷新 (preagg)
4. **自动调度**: APScheduler 按 cron 跑 simulate + preagg refresh

不挂查询路由（config / summary / distinct / drilldown / view_templates）。

启动：
    uvicorn app.main_generation:app --host 0.0.0.0 --port 8002

调度由 env 变量配置：
    SIM_CRON="0 */2 * * *"       每 2 小时跑 simulate
    PREAGG_CRON="10 */2 * * *"   比 sim 晚 10 分钟刷 preagg
    SIM_DAYS_BACK=1
    SIM_RUNS_PER_CALL=1

把对应 cron 设为空字符串即关闭该任务。
"""
from __future__ import annotations
import logging
from contextlib import asynccontextmanager
from datetime import date, timedelta

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from .settings import settings
from .db import init_db, SessionLocal
from .api import ingest, admin


logger = logging.getLogger("ai-metrics.generation")


_scheduler: AsyncIOScheduler | None = None


def _do_simulate():
    """调度器调用：模拟最近 N 天数据。"""
    try:
        from .sources.simulate import run as sim_run, SOURCES
        days = max(1, settings.sim_days_back)
        runs = max(1, settings.sim_runs_per_call)
        logger.info(f"[scheduled] simulate sources=all days={days} runs/day={runs}")
        sim_run(list(SOURCES.keys()), days, reset=False, runs_per_day=runs)
        logger.info("[scheduled] simulate done")
    except Exception as e:
        logger.exception(f"[scheduled] simulate FAILED: {e}")


def _do_preagg():
    """调度器调用：刷新预聚合最近 60 天。"""
    try:
        from .services.preagg import refresh as preagg_refresh
        today = date.today()
        d_from, d_to = today - timedelta(days=60), today
        with SessionLocal() as db:
            deleted, inserted = preagg_refresh(d_from, d_to, db)
        logger.info(f"[scheduled] preagg refresh deleted={deleted} inserted={inserted}")
    except Exception as e:
        logger.exception(f"[scheduled] preagg refresh FAILED: {e}")


def _parse_cron(s: str) -> CronTrigger | None:
    """5 字段 cron 字符串 → CronTrigger；空字符串/None → None (关闭任务)。"""
    if not s or not s.strip():
        return None
    parts = s.split()
    if len(parts) != 5:
        logger.warning(f"invalid cron format (need 5 fields): {s!r}")
        return None
    minute, hour, day, month, dow = parts
    return CronTrigger(minute=minute, hour=hour, day=day, month=month, day_of_week=dow)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    global _scheduler
    _scheduler = AsyncIOScheduler()

    sim_trigger = _parse_cron(settings.sim_cron)
    if sim_trigger:
        _scheduler.add_job(_do_simulate, sim_trigger, id="simulate",
                           coalesce=True, max_instances=1, replace_existing=True)
        logger.info(f"scheduled simulate: cron={settings.sim_cron!r}")
    else:
        logger.info("simulate scheduling disabled (sim_cron empty)")

    preagg_trigger = _parse_cron(settings.preagg_cron)
    if preagg_trigger:
        _scheduler.add_job(_do_preagg, preagg_trigger, id="preagg",
                           coalesce=True, max_instances=1, replace_existing=True)
        logger.info(f"scheduled preagg: cron={settings.preagg_cron!r}")
    else:
        logger.info("preagg scheduling disabled (preagg_cron empty)")

    _scheduler.start()
    logger.info(f"generation service ready; {len(_scheduler.get_jobs())} jobs armed")
    yield
    _scheduler.shutdown(wait=False)


app = FastAPI(title="AI 测试度量看板 · 报表生成服务", version="0.4.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.cors_origins] if settings.cors_origins != "*" else ["*"],
    allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
)

# 只挂写入 / 管理路由（**绝不挂 query 路由**）
app.include_router(ingest.router, prefix=settings.api_prefix, tags=["ingest"])
app.include_router(admin.router,  prefix=settings.api_prefix, tags=["admin"])


@app.get("/")
def root():
    jobs = []
    if _scheduler:
        jobs = [{"id": j.id, "next_run": str(j.next_run_time)} for j in _scheduler.get_jobs()]
    return {
        "name": "ai-metrics-generation",
        "mode": "generation-only (no query)",
        "api": settings.api_prefix,
        "docs": "/docs",
        "scheduled_jobs": jobs,
    }


@app.get("/healthz")
def healthz():
    return {"status": "ok"}
