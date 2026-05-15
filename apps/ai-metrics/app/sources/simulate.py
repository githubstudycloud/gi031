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

from sqlalchemy import select, delete, func

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


def _next_version(db, period_date, source):
    """取该 (date, source) 下一个 version_no（max + 1，从 1 起）。"""
    cur = db.execute(
        select(func.coalesce(func.max(AiMetric.version_no), 0))
        .where(AiMetric.period_date == period_date, AiMetric.source == source)
    ).scalar()
    return int(cur or 0) + 1


def _insert(db, **row):
    """每次 simulate 都写入新 version_no，因此不需要 upsert，直接 insert。"""
    db.execute(AiMetric.__table__.insert().values(**row))


def simulate_source_day(db, src_key, the_date, projects, domains, detail_per_combo=8):
    rules = SOURCES[src_key]
    version_no = _next_version(db, the_date, src_key)
    inserted = skipped = 0
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

            # 偶尔模拟漏报（2%）—— version_no 已固定
            for m, v in vals.items():
                if random.random() < 0.02:
                    skipped += 1
                    continue
                _insert(db,
                    period_date=the_date, project_code=proj, domain_code=dom,
                    iteration_code=None, org_path=None,
                    metric_code=m, metric_value=float(v),
                    source=src_key, version_no=version_no,
                )
                inserted += 1

            # 明细（详情）—— 改为每个 ai_x 指标 × proj × domain 写 detail_per_combo 条
            # 默认 8 条 × 4 source × 6 domain × 4 proj × 1 day = 768 条/天，够分页+筛选演示
            for m_code, detail_type, label_tpl in [
                ("ai_case_count",  "case",        "AI 用例"),
                ("ai_req_count",   "requirement", "AI 需求"),
                ("ai_code_lines",  "code_change", "AI 脚本 PR"),
            ]:
                if m_code not in vals: continue
                count = vals[m_code]
                if count <= 0: continue
                # 写 detail_per_combo 条（不超过指标数本身）
                n = min(detail_per_combo, max(1, int(count) // 2 or 1))
                for i in range(n):
                    detail_seq += 1
                    severity = random.choice(["high","medium","low","low","low"])
                    author = random.choice(["alice","bob","charlie","dave","eve","frank"])
                    db.add(AiMetricDetail(
                        period_date=the_date, project_code=proj, domain_code=dom,
                        metric_code=m_code, detail_type=detail_type,
                        source=src_key, version_no=version_no,
                        ref_id=f"{src_key.upper()}-{detail_type[:3].upper()}-{proj[-1].upper()}{dom[0].upper()}-{the_date.strftime('%m%d')}-{version_no}{i:03d}",
                        ref_label=f"[{rules['label']}] {label_tpl} {dom}/{i+1} (v{version_no})",
                        ref_url=f"https://{src_key}.example.com/{detail_type}/{detail_seq}",
                        is_ai_generated=True,
                        is_adopted=(random.random() < 0.65) if m_code == "ai_case_count" else None,
                        severity=severity if detail_type != "code_change" else None,
                        author=author,
                        payload={"source": src_key, "scrape_at": the_date.isoformat(), "version": version_no},
                    ))
    return inserted, skipped, version_no


def run(src_keys, days_back, reset=False, runs_per_day=1, detail_per_combo=8):
    """对每个 (src, day) 组合跑 runs_per_day 次，version_no 自动 +1。

    runs_per_day=1 表示每个 day 每 source 只产生 1 个版本（默认）
    runs_per_day=3 表示每天每 source 跑 3 次（模拟早中晚各拉一次） → version_no=1,2,3
    """
    init_db()
    with SessionLocal() as db:
        projects = [p.code for p in db.execute(select(DimProject)).scalars().all()]
        domains  = [d.code for d in db.execute(select(DimDomain)).scalars().all()]
        if not projects or not domains:
            print("⚠ no projects/domains seeded — run `python -m app.seed.fake_data` first")
            return

        if reset:
            db.execute(delete(AiMetric).where(AiMetric.source.in_(src_keys)))
            db.execute(delete(AiMetricDetail).where(AiMetricDetail.source.in_(src_keys)))
            db.commit()
            print(f"reset facts for sources={src_keys}")

        today = date.today()
        for d_off in range(days_back):
            the_date = today - timedelta(days=d_off)
            for sk in src_keys:
                for run_idx in range(runs_per_day):
                    ins, skp, ver = simulate_source_day(db, sk, the_date, projects, domains, detail_per_combo)
                    db.commit()
                    print(f"  {the_date}  [{sk:>9}] v{ver}  +{ins} ins  {skp} skip")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="all", help="all | jira | testrail | gitlab | sonar | comma-list")
    ap.add_argument("--days", type=int, default=1, help="模拟最近 N 天")
    ap.add_argument("--backfill", type=int, default=None, help="历史回填天数（覆盖 --days）")
    ap.add_argument("--runs-per-day", type=int, default=1, help="每天每来源跑几次（多版本演示）")
    ap.add_argument("--detail-per-combo", type=int, default=8, help="每个 (ai_metric × proj × domain) 写多少条 detail")
    ap.add_argument("--reset", action="store_true", help="先删除对应来源的事实再灌")
    args = ap.parse_args()

    days = args.backfill or args.days
    keys = list(SOURCES.keys()) if args.source == "all" else args.source.split(",")
    for k in keys:
        if k not in SOURCES:
            raise SystemExit(f"unknown source: {k}; available: {list(SOURCES.keys())}")
    print(f"== simulate sources={keys} days={days} runs/day={args.runs_per_day} detail/combo={args.detail_per_combo} reset={args.reset}")
    run(keys, days, args.reset, args.runs_per_day, args.detail_per_combo)


if __name__ == "__main__":
    main()
