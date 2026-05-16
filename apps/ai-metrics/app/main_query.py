"""**查询独立模块**入口（只读 API）。

V4：
- **不挂 admin 路由 / 不挂 admin 静态**（admin UI 应该开在 generation 服务，避免误把读副本当写入口）
- 仅前端展示静态：/ui /vue /react /vendor
- **不挂 ingest**：防止误调用写坏数据

启动：
    uvicorn app.main_query:app --host 0.0.0.0 --port 8001

挂的路由：
    /api/healthz                                  (meta)
    /api/dropdowns/projects                       (meta)
    /api/reports/{type}/config                    (config)
    /api/reports/{type}/summary | distinct | drilldown  (data)
    /api/view_templates/{type}                    (view_templates)

不挂：/api/metrics/* (ingest)、/api/admin/* (admin)
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


app = FastAPI(title="AI 测试度量看板 · 查询模块", version="0.4.0", lifespan=lifespan)

# CORS：`*` 与 credentials 不可共存
_cors_is_wildcard = (settings.cors_origins.strip() == "*")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if _cors_is_wildcard else [
        o.strip() for o in settings.cors_origins.split(",") if o.strip()
    ],
    allow_credentials=not _cors_is_wildcard,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(meta.router,       prefix=settings.api_prefix, tags=["meta"])
app.include_router(config_api.router, prefix=settings.api_prefix, tags=["config"])
app.include_router(data.router,       prefix=settings.api_prefix, tags=["data"])
app.include_router(vt_api.router,     prefix=settings.api_prefix, tags=["view-templates"])


# 静态前端：只挂展示用 UI；**admin 不挂**（避免读副本暴露 admin 页面）
_root = Path(__file__).parent.parent
for mount_path, sub in [("/ui", "prototype"), ("/vue", "frontend/vue"),
                        ("/react", "frontend/react"), ("/vendor", "frontend/vendor")]:
    p = _root / sub
    if p.exists():
        app.mount(mount_path, StaticFiles(directory=str(p), html=True), name=sub.replace("/", "_"))


@app.get("/")
def root():
    return {
        "name": "ai-metrics-query",
        "mode": "query-only (no ingest, no admin)",
        "ui_html":  "/ui/", "ui_vue": "/vue/", "ui_react": "/react/",
        "admin_hint": "open admin on the generation service (typically :8002/admin/)",
        "api": settings.api_prefix, "docs": "/docs",
    }
