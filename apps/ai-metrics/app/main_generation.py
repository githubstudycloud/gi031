"""**报表生成服务** (Generation Service)。

与 `app.main_query` (只读) 配套；本服务负责：
1. 数据写入 (ingest, simulate)
2. Admin 元数据 CRUD (project / domain / project_domain / metric_def)
3. 预聚合刷新 (preagg)
4. **自动调度**: APScheduler 按 cron 跑 simulate + preagg refresh
5. **Admin UI**: 静态托管 admin/index.html（与 admin API 同 origin，避免 CORS 复杂度）

不挂查询路由（config / summary / distinct / drilldown / view_templates）。

启动：
    uvicorn app.main_generation:app --host 0.0.0.0 --port 8002

调度由 env 变量配置：
    SIM_CRON="0 */2 * * *"       每 2 小时跑 simulate
    PREAGG_CRON="10 */2 * * *"   比 sim 晚 10 分钟刷 preagg
    SIM_DAYS_BACK=1
    SIM_RUNS_PER_CALL=1

把对应 cron 设为空字符串即关闭该任务。

⚠ **多 worker 警告**：APScheduler 在每个 worker 内独立跑，多 worker 会让 simulate × N 倍写入。
   本服务**必须以 `--workers 1` 启动**；compose 已锁定，运维改 workers 数需另接分布式锁。
"""
from __future__ import annotations
import logging
import os
from contextlib import asynccontextmanager
from datetime import date, timedelta
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

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
        logger.info(f"[scheduled] simulate sources=all days={days} runs/day={runs} pid={os.getpid()}")
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
        logger.info(f"[scheduled] preagg refresh deleted={deleted} inserted={inserted} pid={os.getpid()}")
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
    if not settings.admin_token:
        logger.warning("ADMIN_TOKEN unset — admin/ingest endpoints are OPEN (dev mode). "
                       "Set ADMIN_TOKEN env to enable X-Admin-Token gating.")

    # 局部持有，避免多 TestClient 共享 app 时 global _scheduler 被覆盖、
    # 旧 lifespan shutdown 时 raise SchedulerNotRunningError
    sched = AsyncIOScheduler()
    global _scheduler
    _scheduler = sched

    sim_trigger = _parse_cron(settings.sim_cron)
    if sim_trigger:
        sched.add_job(_do_simulate, sim_trigger, id="simulate",
                      coalesce=True, max_instances=1, replace_existing=True)
        logger.info(f"scheduled simulate: cron={settings.sim_cron!r}")
    else:
        logger.info("simulate scheduling disabled (sim_cron empty)")

    preagg_trigger = _parse_cron(settings.preagg_cron)
    if preagg_trigger:
        sched.add_job(_do_preagg, preagg_trigger, id="preagg",
                      coalesce=True, max_instances=1, replace_existing=True)
        logger.info(f"scheduled preagg: cron={settings.preagg_cron!r}")
    else:
        logger.info("preagg scheduling disabled (preagg_cron empty)")

    sched.start()
    logger.info(f"generation service ready; {len(sched.get_jobs())} jobs armed; pid={os.getpid()}")
    try:
        yield
    finally:
        if sched.running:
            sched.shutdown(wait=False)


app = FastAPI(title="AI 测试度量看板 · 报表生成服务", version="0.4.0", lifespan=lifespan)

# CORS：admin UI 与 admin API 同 origin（都在 generation），通常不需要跨域；
# 仍然保留宽松配置以兼容 query 服务前端嵌入 admin iframe 的场景
_cors_is_wildcard = (settings.cors_origins.strip() == "*")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if _cors_is_wildcard else [
        o.strip() for o in settings.cors_origins.split(",") if o.strip()
    ],
    allow_credentials=not _cors_is_wildcard,
    allow_methods=["*"], allow_headers=["*"],
)

# 只挂写入 / 管理路由（**绝不挂 query 路由**）
app.include_router(ingest.router, prefix=settings.api_prefix, tags=["ingest"])
app.include_router(admin.router,  prefix=settings.api_prefix, tags=["admin"])


# V4: admin UI 与跨服务跳转用到的 UI 都挂上（同 origin = 无 CORS / 无 GEN_URL 推断）
_root = Path(__file__).parent.parent
for mount_path, sub in [("/ui", "prototype"), ("/vue", "frontend/vue"),
                        ("/react", "frontend/react"), ("/vendor", "frontend/vendor"),
                        ("/admin", "admin")]:
    p = _root / sub
    if p.exists():
        app.mount(mount_path, StaticFiles(directory=str(p), html=True), name=sub.replace("/", "_"))


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
        "admin": "/admin/",
        "scheduled_jobs": jobs,
        "has_admin_auth": bool(settings.admin_token),
    }


@app.get("/healthz")
def healthz():
    return {"status": "ok"}
