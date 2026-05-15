"""预聚合：把 ai_metric (long-table × 多版本) 卷成 report_fact_ai_metrics_daily。

设计：
- 只算 ATOMIC_METRICS（sum-able）。computed/weighted_avg 列由查询层用原子值再算。
- 受 latest_valid_per_(date,source) 版本拣选影响。
- 一次 GROUP BY (period_date, project_code, domain_code, metric_code) → 一行
- 刷新策略：DELETE WHERE period_date BETWEEN ... + INSERT new
- 部署：cron 每日凌晨刷一次；也可 simulate 完自动 refresh

性能预期（demo 量级）：
  long-table  ~8000 行 → 每次 summary GROUP BY 全表
  preagg      ~400  行 (10 天 × 4 proj × 6 dom × 6 metric) → 直接 SELECT

CLI:
    python -m app.services.preagg refresh                  # 默认最近 60 天
    python -m app.services.preagg refresh --days 365       # 全量
    python -m app.services.preagg refresh --date 2026-05-15  # 单日
"""
from __future__ import annotations
import argparse
from datetime import date, timedelta
from sqlalchemy import select, func, and_, delete
from sqlalchemy.orm import Session

from ..db import SessionLocal, init_db
from ..models import AiMetric, AiMetricInvalidMark, ReportFactDaily
from .metric_query import ATOMIC_METRICS


def refresh(date_from: date, date_to: date, db: Session | None = None) -> tuple[int, int]:
    """重算并落 [date_from, date_to] 区间的 preagg。返回 (deleted, inserted)。"""
    if db is None:
        with SessionLocal() as _db:
            return refresh(date_from, date_to, _db)

    # 1) 先删除该区间已有的预聚合
    deleted = db.execute(
        delete(ReportFactDaily).where(
            ReportFactDaily.period_date >= date_from,
            ReportFactDaily.period_date <= date_to,
        )
    ).rowcount or 0

    # 2) latest_valid 子查询 (与 metric_query 完全一致的逻辑)
    valid_sq = (
        select(
            AiMetric.period_date.label("d"),
            AiMetric.source.label("s"),
            func.max(AiMetric.version_no).label("v"),
        )
        .select_from(AiMetric)
        .outerjoin(
            AiMetricInvalidMark,
            and_(
                AiMetricInvalidMark.period_date == AiMetric.period_date,
                AiMetricInvalidMark.source == AiMetric.source,
                AiMetricInvalidMark.version_no == AiMetric.version_no,
            ),
        )
        .where(AiMetricInvalidMark.id.is_(None))
        .where(AiMetric.period_date >= date_from, AiMetric.period_date <= date_to)
        .group_by(AiMetric.period_date, AiMetric.source)
    ).subquery("vv")

    # 3) 聚合
    agg = (
        select(
            AiMetric.period_date,
            AiMetric.project_code,
            AiMetric.domain_code,
            AiMetric.metric_code,
            func.sum(AiMetric.metric_value).label("v"),
        )
        .join(valid_sq, and_(
            AiMetric.period_date == valid_sq.c.d,
            AiMetric.source == valid_sq.c.s,
            AiMetric.version_no == valid_sq.c.v,
        ))
        .where(
            AiMetric.metric_code.in_(ATOMIC_METRICS),
            AiMetric.period_date >= date_from,
            AiMetric.period_date <= date_to,
        )
        .group_by(
            AiMetric.period_date, AiMetric.project_code,
            AiMetric.domain_code, AiMetric.metric_code,
        )
    )

    rows = db.execute(agg).all()
    inserted = 0
    for pd, proj, dom, mc, val in rows:
        db.execute(
            ReportFactDaily.__table__.insert().values(
                period_date=pd, project_code=proj, domain_code=dom,
                metric_code=mc, metric_value=float(val or 0),
            )
        )
        inserted += 1
    db.commit()
    return deleted, inserted


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["refresh"])
    ap.add_argument("--days", type=int, default=60, help="刷新最近 N 天")
    ap.add_argument("--date", type=str, default=None, help="只刷一天 YYYY-MM-DD")
    args = ap.parse_args()

    init_db()
    if args.date:
        d = date.fromisoformat(args.date)
        deleted, inserted = refresh(d, d)
    else:
        today = date.today()
        deleted, inserted = refresh(today - timedelta(days=args.days), today)
    print(f"preagg refresh: deleted={deleted} inserted={inserted}")


if __name__ == "__main__":
    main()
