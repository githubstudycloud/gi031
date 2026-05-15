from datetime import datetime, date, timezone


def _utc_now() -> datetime:
    """timezone-aware UTC now（替代 datetime.utcnow()，3.12 起 deprecated）"""
    return datetime.now(timezone.utc).replace(tzinfo=None)  # 存入 DB 时不带 tz（与已有列保持兼容）
from sqlalchemy import (
    BigInteger, String, Integer, Date, DateTime, Boolean, Numeric, JSON,
    UniqueConstraint, Index, ForeignKey, text
)
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base, MYSQL_TABLE_ARGS


# ────── 维度 ──────

class DimProject(Base):
    __tablename__ = "dim_project"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now)
    __table_args__ = (MYSQL_TABLE_ARGS,)


class DimDomain(Base):
    __tablename__ = "dim_domain"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(255))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    __table_args__ = (MYSQL_TABLE_ARGS,)


# 预留：V1.1 / V1.2 扩展
class DimIteration(Base):
    __tablename__ = "dim_iteration"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(255))
    project_code: Mapped[str] = mapped_column(String(64))
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    __table_args__ = (UniqueConstraint("project_code", "code", name="uq_proj_iter"), MYSQL_TABLE_ARGS)


class DimOrg(Base):
    __tablename__ = "dim_org"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(255))
    parent_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    depth: Mapped[int] = mapped_column(Integer)
    path: Mapped[str] = mapped_column(String(512))
    __table_args__ = (MYSQL_TABLE_ARGS,)


# ────── 指标定义（数据驱动，加指标不发版）──────

class MetricDef(Base):
    __tablename__ = "metric_def"
    code: Mapped[str] = mapped_column(String(64), primary_key=True)
    label: Mapped[str] = mapped_column(String(255))
    category: Mapped[str] = mapped_column(String(64))                     # 测试设计 / 测试脚本生成
    unit: Mapped[str | None] = mapped_column(String(32), nullable=True)
    data_type: Mapped[str] = mapped_column(String(16))                    # int / decimal / percent
    agg_method: Mapped[str] = mapped_column(String(32))                   # sum / avg / weighted_avg / computed
    computed_formula: Mapped[str | None] = mapped_column(String(255), nullable=True)
    weight_metric: Mapped[str | None] = mapped_column(String(64), nullable=True)
    drilldown_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    is_default_visible: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    __table_args__ = (MYSQL_TABLE_ARGS,)


# ────── 事实表（长表）──────

class AiMetric(Base):
    __tablename__ = "ai_metric"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    period_date: Mapped[date] = mapped_column(Date, index=True)
    project_code: Mapped[str] = mapped_column(String(64))
    domain_code: Mapped[str] = mapped_column(String(64))
    iteration_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    org_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    metric_code: Mapped[str] = mapped_column(String(64))
    metric_value: Mapped[float] = mapped_column(Numeric(18, 4))
    source: Mapped[str] = mapped_column(String(64), default="manual")
    # 多版本：同 (period_date, source) 一天可有多个 version_no；查询默认取 latest valid
    version_no: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now)
    __table_args__ = (
        UniqueConstraint("period_date", "project_code", "domain_code",
                         "iteration_code", "metric_code", "source", "version_no",
                         name="uq_metric"),
        Index("idx_proj_domain", "project_code", "domain_code"),
        Index("idx_metric", "metric_code"),
        Index("idx_date_source_ver", "period_date", "source", "version_no"),
        MYSQL_TABLE_ARGS,
    )


class AiMetricInvalidMark(Base):
    """标记某 (period_date, source, version_no) 失效；查询默认跳过被标失效的版本。"""
    __tablename__ = "ai_metric_invalid_mark"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    period_date: Mapped[date] = mapped_column(Date)
    source: Mapped[str] = mapped_column(String(64))
    version_no: Mapped[int] = mapped_column(Integer)
    marked_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    marked_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now)
    __table_args__ = (
        UniqueConstraint("period_date", "source", "version_no", name="uq_invalid"),
        Index("idx_invalid_date_src", "period_date", "source"),
        MYSQL_TABLE_ARGS,
    )


class UserRowFavorite(Base):
    """(user, report, row_key_json) 收藏。查询时 join 给每行 _row_favorite=true。"""
    __tablename__ = "user_row_favorite"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64))
    report_type: Mapped[str] = mapped_column(String(64))
    row_key: Mapped[str] = mapped_column(String(512))   # JSON-stringified, e.g. '{"domain_code":"core"}'
    favorited_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now)
    __table_args__ = (
        UniqueConstraint("user_id", "report_type", "row_key", name="uq_row_fav"),
        Index("idx_user_report", "user_id", "report_type"),
        MYSQL_TABLE_ARGS,
    )


class ViewTemplate(Base):
    """视图模板：density / page_size / paging_mode / tree_mode / show_kpi / only_columns / default_sort."""
    __tablename__ = "view_template"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(255))
    report_type: Mapped[str] = mapped_column(String(64))
    scope: Mapped[str] = mapped_column(String(16), default="global")   # global / org / role / user
    scope_owner: Mapped[str | None] = mapped_column(String(255), nullable=True)
    config: Mapped[dict] = mapped_column(JSON)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now)
    __table_args__ = (MYSQL_TABLE_ARGS,)


class AiMetricDetail(Base):
    """下钻明细。点击 个数 / 行数 类列时展开。带 version_no，与 ai_metric 一致。"""
    __tablename__ = "ai_metric_detail"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    period_date: Mapped[date] = mapped_column(Date)
    project_code: Mapped[str] = mapped_column(String(64))
    domain_code: Mapped[str] = mapped_column(String(64))
    iteration_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    metric_code: Mapped[str] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(64), default="manual", server_default="manual")
    version_no: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    detail_type: Mapped[str] = mapped_column(String(64))   # requirement / case / code_change
    ref_id: Mapped[str] = mapped_column(String(128))
    ref_label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ref_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    is_ai_generated: Mapped[bool] = mapped_column(Boolean, default=False)
    is_adopted: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    severity: Mapped[str | None] = mapped_column(String(32), nullable=True)    # high/medium/low (筛选示例)
    author: Mapped[str | None] = mapped_column(String(64), nullable=True)
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now)
    __table_args__ = (
        Index("idx_drill", "period_date", "project_code", "domain_code", "metric_code"),
        Index("idx_drill_src_ver", "period_date", "source", "version_no"),
        MYSQL_TABLE_ARGS,
    )
