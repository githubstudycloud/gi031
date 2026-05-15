"""模拟数据源 → 入表

真实场景每个来源会被一个独立 cron / generation-service worker 拉取：
  - Jira          → 需求数 + AI 需求数
  - TestRail      → 用例数 + AI 用例数 + AI 用例采纳数
  - Gitlab MR     → 代码行数 + AI 行数 + 新脚本数 + 新脚本 AI 辅助数
  - SonarQube     → AI 代码准确率分子（无 bug 行数）

V1 模拟器：对所有 (项目, 领域, 日期) 组合按规则生成，写入 ai_metric (source=<src>)。
upsert 语义复用 /api/metrics/ingest 的事务。
重复跑同一 (date, project, domain, metric, source) → 更新 metric_value（幂等）。

用法：
    python -m app.sources.simulate --source jira --days 1
    python -m app.sources.simulate --source all  --days 7
    python -m app.sources.simulate --source all  --backfill 30 --reset
"""
from __future__ import annotations
import argparse
import random
from datetime import date, timedelta

from sqlalchemy import select, delete

from app.db import SessionLocal, init_db
from app.models import AiMetric, AiMetricDetail, DimProject, DimDomain


# 每个来源负责的"原子"指标 + 大致量级
SOURCES = {
    "jira": {
        "label": "Jira",
        "metrics": {
            "req_count":     (3, 18),
            "ai_req_count":  (0, 12),  # 受 req_count 上界约束（生成时再截断）
        },
    },
    "testrail": {
        "label": "TestRail",
        "metrics": {
            "new_case_count":  (10, 50),
            "ai_case_count":   (3, 35),
            "ai_case_adopted": (0, 30),
        },
    },
    "gitlab": {
        "label": "Gitlab MR",
        "metrics": {
            "total_code_lines":          (200, 1200),
            "ai_code_lines":             (50, 800),
            "new_script_count":          (1, 8),
            "new_script_ai_assisted_count": (0, 6),
        },
    },
    "sonar": {
        "label": "SonarQube",
        "metrics": {
            "ai_code_accurate_lines": (40, 750),
        },
    },
}


def _upsert(db, **row):
    """跨 DB 简单 upsert：按唯一键查，存在则 update，否则 insert。"""
    existing = db.execute(
        select(AiMetric.id).where(
            AiMetric.period_date == row["period_date"],
            AiMetric.project_code == row["project_code"],
            AiMetric.domain_code == row["domain_code"],
            (AiMetric.iteration_code.is_(None) if row.get("iteration_code") is None
             else AiMetric.iteration_code == row["iteration_code"]),
            AiMetric.metric_code == row["metric_code"],
            AiMetric.source == row["source"],
        )
    ).first()
    if existing:
        db.execute(
            AiMetric.__table__.update().where(AiMetric.id == existing[0]).values(metric_value=row["metric_value"])
        )
        return False
    db.execute(AiMetric.__table__.insert().values(**row))
    return True


def simulate_source_day(db, src_key, the_date, projects, domains):
    rules = SOURCES[src_key]
    inserted = updated = skipped = 0
    detail_seq = 0
    for proj in projects:
        for dom in domains:
            # 计算"原子"指标 —— 保证约束（AI ≤ 总数）
            vals = {}
            for m, (lo, hi) in rules["metrics"].items():
                vals[m] = random.randint(lo, hi)
            # 约束修正（业务规则）
            if "ai_req_count" in vals and "req_count" in vals:
                vals["ai_req_count"] = min(vals["ai_req_count"], vals["req_count"])
            if "ai_case_count" in vals and "new_case_count" in vals:
                vals["ai_case_count"] = min(vals["ai_case_count"], vals["new_case_count"])
            if "ai_case_adopted" in vals and "ai_case_count" in vals:
                vals["ai_case_adopted"] = min(vals["ai_case_adopted"], vals["ai_case_count"])
            if "ai_code_lines" in vals and "total_code_lines" in vals:
                vals["ai_code_lines"] = min(vals["ai_code_lines"], vals["total_code_lines"])
            if "new_script_ai_assisted_count" in vals and "new_script_count" in vals:
                vals["new_script_ai_assisted_count"] = min(vals["new_script_ai_assisted_count"], vals["new_script_count"])
            # sonar 准确行依赖 gitlab 的 ai_code_lines：跨源依赖 → 真实场景里 merger 处理；
            # 模拟时按经验 0.85~1.0 比例（这里就近假定 ai_code_lines 是 ai_code_accurate_lines * (1/k)）
            if "ai_code_accurate_lines" in vals:
                # 限制在合理范围；真实数据应从 gitlab 来源的 ai_code_lines 算
                pass

            # 偶尔模拟漏报（2%）
            for m, v in vals.items():
                if random.random() < 0.02:
                    skipped += 1
                    continue
                row = dict(
                    period_date=the_date, project_code=proj, domain_code=dom,
                    iteration_code=None, org_path=None,
                    metric_code=m, metric_value=float(v),
                    source=src_key,
                )
                if _upsert(db, **row): inserted += 1
                else: updated += 1

            # 部分明细（仅前 2 天，避免太多）
            if (date.today() - the_date).days < 2:
                if "ai_case_count" in vals:
                    for i in range(min(vals["ai_case_count"], 3)):
                        detail_seq += 1
                        db.add(AiMetricDetail(
                            period_date=the_date, project_code=proj, domain_code=dom,
                            metric_code="ai_case_count", detail_type="case",
                            ref_id=f"{src_key.upper()}-CASE-{proj}-{dom}-{the_date}-{i}",
                            ref_label=f"[{rules['label']}] AI 用例 {dom}/{i+1}",
                            ref_url=f"https://{src_key}.example.com/case/{detail_seq}",
                            is_ai_generated=True,
                            payload={"source": src_key, "scrape_at": the_date.isoformat()},
                        ))
                if "ai_code_lines" in vals and vals["ai_code_lines"] > 0:
                    detail_seq += 1
                    db.add(AiMetricDetail(
                        period_date=the_date, project_code=proj, domain_code=dom,
                        metric_code="ai_code_lines", detail_type="code_change",
                        ref_id=f"{src_key.upper()}-MR-{proj}-{the_date}-{detail_seq}",
                        ref_label=f"[{rules['label']}] AI 生成脚本 PR #{detail_seq}",
                        ref_url=f"https://{src_key}.example.com/mr/{detail_seq}",
                        is_ai_generated=True,
                        payload={"lines": vals["ai_code_lines"]},
                    ))
    return inserted, updated, skipped


def run(src_keys, days_back, reset=False):
    init_db()
    with SessionLocal() as db:
        projects = [p.code for p in db.execute(select(DimProject)).scalars().all()]
        domains  = [d.code for d in db.execute(select(DimDomain)).scalars().all()]
        if not projects or not domains:
            print("⚠ no projects/domains seeded — run `python -m app.seed.fake_data` first")
            return

        if reset:
            db.execute(delete(AiMetric).where(AiMetric.source.in_(src_keys)))
            db.execute(delete(AiMetricDetail))
            db.commit()
            print(f"reset facts for sources={src_keys}")

        today = date.today()
        for d_off in range(days_back):
            the_date = today - timedelta(days=d_off)
            for sk in src_keys:
                ins, upd, skp = simulate_source_day(db, sk, the_date, projects, domains)
                db.commit()
                print(f"  {the_date}  [{sk:>9}]  +{ins} insert  {upd} update  {skp} skip")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="all", help="all | jira | testrail | gitlab | sonar | comma-list")
    ap.add_argument("--days", type=int, default=1, help="模拟最近 N 天，每天每来源一批")
    ap.add_argument("--backfill", type=int, default=None, help="若给定，覆盖 --days，做历史回填")
    ap.add_argument("--reset", action="store_true", help="先删除对应来源的事实再灌")
    args = ap.parse_args()

    days = args.backfill or args.days
    keys = list(SOURCES.keys()) if args.source == "all" else args.source.split(",")
    for k in keys:
        if k not in SOURCES:
            raise SystemExit(f"unknown source: {k}; available: {list(SOURCES.keys())}")
    print(f"== simulate sources={keys} days={days} reset={args.reset}")
    run(keys, days, args.reset)


if __name__ == "__main__":
    main()
