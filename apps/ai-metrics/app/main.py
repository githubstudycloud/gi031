"""**Dev / 单体便利入口**（包含 query + ingest + admin + 调度器）。

⚠ 生产环境请用拆分：
    uvicorn app.main_query:app       --port 8001   # 读
    uvicorn app.main_generation:app  --port 8002   # 写 + APScheduler

本入口在同一进程内挂全部路由（含 admin / ingest），仅供本地开发 / docker 单容器跑 demo。
"""
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from .settings import settings
from .db import init_db
from .api import meta, config as config_api, data, ingest, view_templates as vt_api, admin


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


app = FastAPI(title="AI 测试度量看板 · Dev 单体", version="0.4.0", lifespan=lifespan)

# CORS：`*` 与 credentials 不可共存（浏览器拒绝），自动降级
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
app.include_router(ingest.router,     prefix=settings.api_prefix, tags=["ingest"])
app.include_router(vt_api.router,     prefix=settings.api_prefix, tags=["view-templates"])
app.include_router(admin.router,      prefix=settings.api_prefix, tags=["admin"])


# 静态前端：把 prototype/ + frontend/vue/ + frontend/react/ 挂在不同前缀下
_root = Path(__file__).parent.parent
for mount_path, sub in [("/ui", "prototype"), ("/vue", "frontend/vue"),
                        ("/react", "frontend/react"), ("/vendor", "frontend/vendor"),
                        ("/admin", "admin")]:
    p = _root / sub
    if p.exists():
        app.mount(mount_path, StaticFiles(directory=str(p), html=True), name=sub.replace("/", "_"))


@app.get("/")
def root():
    return {
        "name": "ai-metrics",
        "mode": "dev-monolith (query + ingest + admin)",
        "ui_html":  "/ui/",
        "ui_vue":   "/vue/",
        "ui_react": "/react/",
        "admin":    "/admin/",
        "api": settings.api_prefix, "docs": "/docs",
    }
