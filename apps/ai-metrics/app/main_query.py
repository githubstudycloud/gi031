"""**查询独立模块**入口（只读 API）。

与 `app.main` 区别：本入口**只挂查询路由**，**不挂 ingest**，
适合：
- 只读副本（多实例 ReadModel 模式）
- 灰度发布查询新版本
- 防止误调用 ingest 改坏数据

启动：
    uvicorn app.main_query:app --host 0.0.0.0 --port 8002

挂的路由：
    /api/healthz                                  (meta)
    /api/dropdowns/projects                       (meta)
    /api/reports/{type}/config                    (config)
    /api/reports/{type}/summary | distinct | drilldown  (data)
    /api/view_templates/{type}                    (view_templates)

不挂：/api/metrics/* （ingest 子模块）
"""
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from .settings import settings
from .db import init_db
from .api import meta, config as config_api, data, view_templates as vt_api


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


app = FastAPI(title="AI 测试度量看板 · 查询模块", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.cors_origins] if settings.cors_origins != "*" else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(meta.router,       prefix=settings.api_prefix, tags=["meta"])
app.include_router(config_api.router, prefix=settings.api_prefix, tags=["config"])
app.include_router(data.router,       prefix=settings.api_prefix, tags=["data"])
app.include_router(vt_api.router,     prefix=settings.api_prefix, tags=["view-templates"])


# 静态前端复用同一套
_root = Path(__file__).parent.parent
for mount_path, sub in [("/ui", "prototype"), ("/vue", "frontend/vue"), ("/react", "frontend/react")]:
    p = _root / sub
    if p.exists():
        app.mount(mount_path, StaticFiles(directory=str(p), html=True), name=sub.replace("/", "_"))


@app.get("/")
def root():
    return {
        "name": "ai-metrics-query",
        "mode": "query-only (no ingest)",
        "ui_html":  "/ui/", "ui_vue": "/vue/", "ui_react": "/react/",
        "api": settings.api_prefix, "docs": "/docs",
    }
