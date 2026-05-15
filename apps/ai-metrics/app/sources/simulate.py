"""模拟数据源 → 入表（**详情先行，metric_value = SUM(detail)**）。

V3 一致性保证：
- 每个 (date, src, proj, domain) 组合先生成 detail 记录
- metric_value 直接由 detail rows COUNT 或 SUM 推导
- 例：req_count = COUNT(detail WHERE detail_type='requirement')
      ai_code_lines = SUM(detail.payload.lines WHERE is_ai AND detail_type='code_change')
- 因此 summary 数字 ≡ drilldown 实际行数 ≡ 业务真相

来源分工：
  jira     → requirement details      → req_count / ai_req_count
  testrail → case details             → new_case_count / ai_case_count / ai_case_adopted
  gitlab   → code_change + script_file → total_code_lines / ai_code_lines /
                                          new_script_count / new_script_ai_assisted_count
  sonar    → code_review details (审计 AI PR) → ai_code_accurate_lines

每次 simulate 对 (date, src) 自动 +1 version_no；多版本可在版本管理界面切换。

用法：
    python -m app.sources.simulate --source all --days 7
    python -m app.sources.simulate --source all --days 30 --runs-per-day 2
    python -m app.sources.simulate --reset
"""
from __future__ import annotations
import argparse
import random
from datetime import date, timedelta

from sqlalchemy import select, delete, func

from app.db import SessionLocal, init_db
from app.models import AiMetric, AiMetricDetail, DimProject, DimDomain


# ─── source 元数据 ─────────────────────────────────────────────
SOURCES = {
    "jira":     {"label": "Jira",      "owns": ["requirement"]},
    "testrail": {"label": "TestRail",  "owns": ["case"]},
    "gitlab":   {"label": "Gitlab MR", "owns": ["code_change", "script_file"]},
    "sonar":    {"label": "SonarQube", "owns": ["code_review"]},
}

# 不同 detail_type 的状态值 (真实业务路径)
STATUS_MAP = {
    "requirement": ["draft", "reviewing", "approved", "in_dev", "done", "rejected"],
    "case":        ["draft", "executable", "passed", "failed", "blocked"],
    "code_change": ["open", "review", "merged", "merged", "merged", "closed"],
    "script_file": ["draft", "merged", "merged", "in_use"],
    "code_review": ["resolved", "resolved", "false_positive", "pending"],
}

AUTHORS = ["alice", "bob", "charlie", "dave", "eve", "frank", "grace", "henry"]


def _next_version(db, period_date, source):
    cur = db.execute(
        select(func.coalesce(func.max(AiMetric.version_no), 0))
        .where(AiMetric.period_date == period_date, AiMetric.source == source)
    ).scalar()
    return int(cur or 0) + 1


def _insert_metric(db, **row):
    db.execute(AiMetric.__table__.insert().values(**row))


def _mk_detail(*, the_date, proj, dom, src, ver, metric_code, detail_type,
               is_ai, status, payload, severity=None, author=None, is_adopted=None,
               ref_id=None, ref_label=None, ref_url=None):
    return AiMetricDetail(
        period_date=the_date, project_code=proj, domain_code=dom,
        metric_code=metric_code, detail_type=detail_type,
        source=src, version_no=ver,
        ref_id=ref_id or f"{src.upper()}-{detail_type[:3].upper()}-{the_date.strftime('%m%d')}-{ver}-{random.randint(1000,9999)}",
        ref_label=ref_label or f"[{SOURCES[src]['label']}] {detail_type} v{ver}",
        ref_url=ref_url or f"https://{src}.example.com/{detail_type}/{random.randint(10000,99999)}",
        is_ai_generated=is_ai, is_adopted=is_adopted,
        severity=severity, author=author or random.choice(AUTHORS), status=status,
        payload=payload,
    )


# ─── 各 source 的"详情→指标"生成器 ────────────────────────────

def gen_jira(db, the_date, proj, dom, src, ver):
    """需求：写 N 条 requirement 详情；req_count = N；ai_req_count = N_ai

    每条详情用 canonical metric_code='req_count' (总集)。
    drilldown 按 (detail_type='requirement', is_ai 过滤可选) 二级映射决定可见集。"""
    n_total = random.randint(5, 18)
    n_ai = 0
    for i in range(n_total):
        is_ai = random.random() < 0.55
        if is_ai: n_ai += 1
        db.add(_mk_detail(
            the_date=the_date, proj=proj, dom=dom, src=src, ver=ver,
            metric_code="req_count",     # canonical
            detail_type="requirement",
            is_ai=is_ai,
            status=random.choice(STATUS_MAP["requirement"]),
            severity=random.choice(["high","medium","low","low","low"]),
            payload={
                "complexity": random.choice(["s","m","l","xl"]),
                "story_points": random.choice([1, 2, 3, 5, 8, 13]),
                "version": ver,
            },
            ref_label=f"[Jira] {'AI ' if is_ai else ''}需求 {dom}-{i+1} v{ver}",
        ))
    return {"req_count": n_total, "ai_req_count": n_ai}


def gen_testrail(db, the_date, proj, dom, src, ver):
    """用例：写 N 条 case 详情；new_case_count=N；ai_case_count=N_ai；ai_case_adopted=N_adopted_ai"""
    n_total = random.randint(15, 50)
    n_ai = 0; n_adopted = 0
    for i in range(n_total):
        is_ai = random.random() < 0.55
        is_adopted = None
        if is_ai:
            n_ai += 1
            is_adopted = random.random() < 0.65
            if is_adopted: n_adopted += 1
        db.add(_mk_detail(
            the_date=the_date, proj=proj, dom=dom, src=src, ver=ver,
            metric_code="new_case_count",   # canonical
            detail_type="case",
            is_ai=is_ai, is_adopted=is_adopted,
            status=random.choice(STATUS_MAP["case"]),
            severity=random.choice(["high","medium","low","low","low"]),
            payload={
                "steps": random.randint(3, 15),
                "last_run": random.choice(["passed","failed","skipped",None]),
                "version": ver,
            },
            ref_label=f"[TestRail] {'AI ' if is_ai else ''}用例 {dom}-{i+1} v{ver}",
        ))
    return {"new_case_count": n_total, "ai_case_count": n_ai, "ai_case_adopted": n_adopted}


def gen_gitlab(db, the_date, proj, dom, src, ver):
    """PR 详情 → total_code_lines/ai_code_lines (按 lines SUM)。
    脚本 详情 → new_script_count/new_script_ai_assisted_count (按数量)。"""
    # PRs
    n_prs = random.randint(2, 10)
    total_lines = 0; ai_lines = 0
    for i in range(n_prs):
        is_ai = random.random() < 0.5
        lines = random.randint(30, 350)
        total_lines += lines
        if is_ai: ai_lines += lines
        db.add(_mk_detail(
            the_date=the_date, proj=proj, dom=dom, src=src, ver=ver,
            metric_code="total_code_lines",  # canonical
            detail_type="code_change",
            is_ai=is_ai,
            status=random.choice(STATUS_MAP["code_change"]),
            payload={
                "lines": lines,
                "files": random.randint(1, 8),
                "lang": random.choice(["python","typescript","go","java"]),
                "version": ver,
            },
            ref_label=f"[Gitlab MR] {'AI ' if is_ai else ''}PR {dom}-{i+1} (+{lines} 行) v{ver}",
        ))
    # Scripts
    n_scripts = random.randint(1, 8)
    n_ai_scripts = 0
    for i in range(n_scripts):
        is_ai = random.random() < 0.45
        if is_ai: n_ai_scripts += 1
        db.add(_mk_detail(
            the_date=the_date, proj=proj, dom=dom, src=src, ver=ver,
            metric_code="new_script_count",   # canonical
            detail_type="script_file",
            is_ai=is_ai,
            status=random.choice(STATUS_MAP["script_file"]),
            payload={"loc": random.randint(20, 400), "version": ver},
            ref_label=f"[Gitlab] {'AI 辅助 ' if is_ai else ''}脚本 {dom}-{i+1} v{ver}",
        ))
    return {
        "total_code_lines": total_lines, "ai_code_lines": ai_lines,
        "new_script_count": n_scripts, "new_script_ai_assisted_count": n_ai_scripts,
    }


def gen_sonar(db, the_date, proj, dom, src, ver, ai_lines_hint=0):
    """SonarQube code_review 详情：每条 review 一段 AI 代码，accurate=true/false 决定是否计入准确行数。
    ai_code_accurate_lines = SUM(reviewed_lines where accurate)。
    ai_lines_hint 来自 gitlab 同 (date, proj, dom) 的 ai_code_lines，sonar review 不超过它。
    """
    if ai_lines_hint <= 0:
        return {}
    # 切成 N 个 review，每个 review 覆盖一段
    n_reviews = random.randint(2, 6)
    accurate_total = 0
    remaining = ai_lines_hint
    for i in range(n_reviews):
        if remaining <= 0: break
        chunk = random.randint(max(10, remaining // (n_reviews - i + 1) // 2),
                               max(20, remaining // (n_reviews - i + 1) * 2))
        chunk = min(chunk, remaining)
        is_accurate = random.random() < 0.88
        if is_accurate:
            accurate_total += chunk
        db.add(_mk_detail(
            the_date=the_date, proj=proj, dom=dom, src=src, ver=ver,
            metric_code="ai_code_accurate_lines",
            detail_type="code_review",
            is_ai=True,
            status=random.choice(STATUS_MAP["code_review"]),
            severity=None if is_accurate else random.choice(["high","medium","low"]),
            payload={
                "reviewed_lines": chunk,
                "accurate": is_accurate,
                "rule": random.choice(["python:S1192","ts:S125","java:S2589","go:S1006"]),
                "version": ver,
            },
            ref_label=f"[Sonar] AI 段审计 {dom}-{i+1} ({chunk}行 {'✓' if is_accurate else '×'}) v{ver}",
        ))
        remaining -= chunk
    return {"ai_code_accurate_lines": accurate_total}


SOURCE_GENERATORS = {
    "jira": gen_jira,
    "testrail": gen_testrail,
    "gitlab": gen_gitlab,
    "sonar": gen_sonar,
}


def simulate_source_day(db, src_key, the_date, projects, domains):
    """对 (date, src) 跑一次，写所有 (proj, domain) 组合的 detail + metric_value。"""
    ver = _next_version(db, the_date, src_key)
    inserted_metric = 0
    detail_count_before = db.execute(
        select(func.count()).select_from(AiMetricDetail)
        .where(AiMetricDetail.period_date == the_date,
               AiMetricDetail.source == src_key,
               AiMetricDetail.version_no == ver)
    ).scalar() or 0

    for proj in projects:
        for dom in domains:
            gen = SOURCE_GENERATORS[src_key]
            if src_key == "sonar":
                # sonar 需要本日同组合的 ai_code_lines 作为上限，先查 gitlab 的本版本
                hint = db.execute(
                    select(func.coalesce(func.sum(AiMetric.metric_value), 0))
                    .where(AiMetric.period_date == the_date,
                           AiMetric.project_code == proj,
                           AiMetric.domain_code == dom,
                           AiMetric.metric_code == "ai_code_lines",
                           AiMetric.source == "gitlab")
                ).scalar() or 0
                counts = gen(db, the_date, proj, dom, src_key, ver, ai_lines_hint=int(hint))
            else:
                counts = gen(db, the_date, proj, dom, src_key, ver)

            for m_code, value in counts.items():
                _insert_metric(db,
                    period_date=the_date, project_code=proj, domain_code=dom,
                    iteration_code=None, org_path=None,
                    metric_code=m_code, metric_value=float(value),
                    source=src_key, version_no=ver,
                )
                inserted_metric += 1

    db.commit()
    detail_count_after = db.execute(
        select(func.count()).select_from(AiMetricDetail)
        .where(AiMetricDetail.period_date == the_date,
               AiMetricDetail.source == src_key,
               AiMetricDetail.version_no == ver)
    ).scalar() or 0
    return inserted_metric, detail_count_after - detail_count_before, ver


def run(src_keys, days_back, reset=False, runs_per_day=1):
    """跑所有指定来源 × N 天 × M 次。注意：sonar 依赖 gitlab 同组合 metric，
    所以 source 顺序固定为 jira → testrail → gitlab → sonar。"""
    init_db()
    # 强制顺序：sonar 最后跑（依赖 gitlab 的 ai_code_lines）
    order = ["jira", "testrail", "gitlab", "sonar"]
    src_keys = [s for s in order if s in src_keys]

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
            for run_idx in range(runs_per_day):
                for sk in src_keys:
                    ins, det, ver = simulate_source_day(db, sk, the_date, projects, domains)
                    print(f"  {the_date}  [{sk:>9}] v{ver}  metric+{ins:>3}  detail+{det:>4}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="all", help="all | jira | testrail | gitlab | sonar | comma-list")
    ap.add_argument("--days", type=int, default=1)
    ap.add_argument("--backfill", type=int, default=None)
    ap.add_argument("--runs-per-day", type=int, default=1)
    ap.add_argument("--reset", action="store_true")
    args = ap.parse_args()

    days = args.backfill or args.days
    keys = list(SOURCES.keys()) if args.source == "all" else args.source.split(",")
    for k in keys:
        if k not in SOURCES:
            raise SystemExit(f"unknown source: {k}; available: {list(SOURCES.keys())}")
    print(f"== simulate (detail-first, metric=SUM detail) sources={keys} days={days} runs/day={args.runs_per_day} reset={args.reset}")
    run(keys, days, args.reset, args.runs_per_day)


if __name__ == "__main__":
    main()
