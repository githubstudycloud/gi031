"""SQLAlchemy engine + session.

DB 解耦：DATABASE_URL 一改即切，SQLAlchemy 自动选驱动 & 方言。
- SQLite (默认):  sqlite:///./ai_metrics.db
- MySQL 8.0:      mysql+pymysql://user:pwd@host:3306/db?charset=utf8mb4
- MySQL 5.7:      同上 (注意 utf8mb4 是必须的)
- PostgreSQL:     postgresql+psycopg://user:pwd@host:5432/db
"""
from sqlalchemy import create_engine, event
from sqlalchemy.orm import declarative_base, sessionmaker, Session

from .settings import settings


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
