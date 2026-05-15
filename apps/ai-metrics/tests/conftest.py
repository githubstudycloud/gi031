"""pytest fixtures: 用临时 SQLite + 一份 seed 数据。"""
from __future__ import annotations
import os
import tempfile
import pytest


@pytest.fixture(scope="session", autouse=True)
def _isolate_db():
    """每次测试 session 用临时 SQLite，互不污染。"""
    fd, path = tempfile.mkstemp(prefix="ai_metrics_test_", suffix=".db")
    os.close(fd)
    os.environ["DATABASE_URL"] = f"sqlite:///{path}"
    yield path
    try: os.remove(path)
    except OSError: pass


@pytest.fixture(scope="session")
def seeded_db(_isolate_db):
    """构建表 + dim/metric_def seed + 4 源 × 2 天 × 2 版本 simulate (detail-first)。"""
    from app.db import init_db, SessionLocal
    from app.seed.fake_data import seed_dimensions
    from app.sources.simulate import run as simulate_run
    init_db()
    with SessionLocal() as db:
        seed_dimensions(db)
    # 4 个来源 × 2 天 × 2 版本，每条 metric 的 value 都 = SUM(对应 detail)
    simulate_run(["jira", "testrail", "gitlab", "sonar"], 2, reset=False, runs_per_day=2)
    return True


@pytest.fixture(scope="session")
def app_query(seeded_db):
    from app.main_query import app
    return app


@pytest.fixture(scope="session")
def client(app_query):
    from fastapi.testclient import TestClient
    return TestClient(app_query)
