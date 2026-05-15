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


app = FastAPI(title="AI 测试度量看板", version="0.1.0", lifespan=lifespan)

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
app.include_router(ingest.router,     prefix=settings.api_prefix, tags=["ingest"])
app.include_router(vt_api.router,     prefix=settings.api_prefix, tags=["view-templates"])
app.include_router(admin.router,      prefix=settings.api_prefix, tags=["admin"])


# 静态前端：把 prototype/ + frontend/vue/ + frontend/react/ 挂在不同前缀下
_root = Path(__file__).parent.parent
for mount_path, sub in [("/ui", "prototype"), ("/vue", "frontend/vue"),
                        ("/react", "frontend/react"), ("/vendor", "frontend/vendor")]:
    p = _root / sub
    if p.exists():
        app.mount(mount_path, StaticFiles(directory=str(p), html=True), name=sub.replace("/", "_"))


@app.get("/")
def root():
    return {
        "name": "ai-metrics",
        "ui_html":  "/ui/",
        "ui_vue":   "/vue/",
        "ui_react": "/react/",
        "api": settings.api_prefix, "docs": "/docs",
    }
