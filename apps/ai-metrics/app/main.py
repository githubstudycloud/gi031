from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from .settings import settings
from .db import init_db
from .api import meta, config as config_api, data, ingest


app = FastAPI(title="AI 测试度量看板", version="0.1.0")


@app.on_event("startup")
def _on_startup():
    init_db()

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


# 静态前端：把 prototype/ 挂在 / 下，方便统一部署
proto_dir = Path(__file__).parent.parent / "prototype"
if proto_dir.exists():
    app.mount("/ui", StaticFiles(directory=str(proto_dir), html=True), name="ui")


@app.get("/")
def root():
    return {"name": "ai-metrics", "ui": "/ui/", "api": settings.api_prefix, "docs": "/docs"}
