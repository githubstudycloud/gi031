"""SQLAlchemy engine + session.

DB 解耦：DATABASE_URL 一改即切，SQLAlchemy 自动选驱动 & 方言。
- SQLite (默认):  sqlite:///./ai_metrics.db
- MySQL 8.0:      mysql+pymysql://user:pwd@host:3306/db?charset=utf8mb4
- MySQL 5.7:      同上 (utf8mb4 + utf8mb4_unicode_ci，5.7/8.0 通用)
- PostgreSQL:     postgresql+psycopg://user:pwd@host:5432/db

**MySQL 5.7 / 8.0 双向兼容约定**：
- 字符集统一 utf8mb4
- collation 统一 utf8mb4_unicode_ci（5.7 默认且 8.0 也支持；避免 8.0 默认的
  utf8mb4_0900_ai_ci 在 5.7 报错）
- 表引擎 InnoDB
- 禁用 8.0 独占语法：CTE / 窗口函数 / generated columns / check constraints /
  functional indexes / utf8mb4_0900_* / INVISIBLE INDEX / JSON_TABLE
"""
from sqlalchemy import create_engine, event
from sqlalchemy.orm import declarative_base, sessionmaker, Session

from .settings import settings


# 所有 ORM 表共享的 MySQL 默认表参数（SQLite/PG 自动忽略）
MYSQL_TABLE_ARGS = {
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_unicode_ci",
    "mysql_engine":  "InnoDB",
}


def _engine_kwargs(url: str) -> dict:
    kw: dict = {"pool_pre_ping": True}
    if url.startswith("sqlite"):
        # SQLite 文件 + 多线程 FastAPI
        kw["connect_args"] = {"check_same_thread": False}
    else:
        # 普通 DB pool 设置
        kw.update(pool_size=8, max_overflow=16, pool_recycle=1800)
    return kw


engine = create_engine(settings.database_url, **_engine_kwargs(settings.database_url))

# SQLite 需要打开外键约束
if settings.database_url.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_conn, _):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
Base = declarative_base()


def get_db() -> Session:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """V1：直接 create_all。生产环境改用 Alembic（见 deployment.md）。"""
    from . import models  # noqa: F401
    Base.metadata.create_all(bind=engine)
