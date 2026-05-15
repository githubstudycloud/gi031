"""填充假数据：4 项目 × 6 领域 × 30 天 × 10 个原子指标 + 明细。

运行：
    python -m app.seed.fake_data            # 默认参数
    python -m app.seed.fake_data --days 60  # 60 天
    python -m app.seed.fake_data --reset    # 先清表再灌
"""
from __future__ import annotations
import argparse
import random
from datetime import date, timedelta

from sqlalchemy.orm import Session
from sqlalchemy import delete

from app.db import SessionLocal, init_db
from app.models import (
    DimProject, DimDomain, MetricDef, AiMetric, AiMetricDetail, ViewTemplate
)


VIEW_TEMPLATES = [
    {
        "code": "weekly_finance", "name": "周度财务汇报",
        "report_type": "ai_metrics", "scope": "global", "sort_order": 1,
        "config": {
            "density": "normal", "page_size": 200, "paging_mode": "client",
            "show_kpi": True,
            "kpi": [
                {"label": "AI 用例数", "source": "totals.ai_case_count", "format": {"kind": "number", "thousand": True}},
                {"label": "AI 入库行", "source": "totals.ai_code_lines", "format": {"kind": "number", "thousand": True, "unit": "行"}},
                {"label": "AI 用例采纳率", "source": "totals.ai_case_adoption_rate", "format": {"kind": "percent"}},
            ],
            "only_columns": [
                "domain_code", "req_count", "ai_req_count", "ai_req_coverage",
                "ai_case_count", "ai_case_adoption_rate", "ai_code_lines"
            ],
            "default_sort": {"field": "ai_code_lines", "dir": "desc"},
        }
    },
    {
        "code": "script_focus", "name": "脚本专项 (技术评审)",
        "report_type": "ai_metrics", "scope": "global", "sort_order": 2,
        "config": {
            "density": "compact", "page_size": 50, "paging_mode": "server",
            "only_columns": [
                "domain_code", "ai_script_code_ratio", "ai_code_lines",
                "ai_code_accuracy", "new_script_ai_ratio", "new_script_count"
            ],
            "default_sort": {"field": "ai_code_accuracy", "dir": "desc"},
        }
    },
]


PROJECTS = [
    ("proj_alpha", "Alpha 内容平台"),
    ("proj_beta",  "Beta 交易中台"),
    ("proj_gamma", "Gamma 风控引擎"),
    ("proj_delta", "Delta 数据中台"),
]

DOMAINS = [
    ("core",      "核心域", 1),
    ("business",  "业务域", 2),
    ("platform",  "平台域", 3),
    ("data",      "数据域", 4),
    ("infra",     "基础设施", 5),
    ("frontend",  "前端域", 6),
]

# 指标定义（数据驱动，加指标改这里）
METRIC_DEFS = [
    # ── 测试设计 ──
    ("req_count",             "需求个数",           "测试设计", "个",  "int",     "sum",          None, None,                True,  True,  10),
    ("ai_req_count",          "AI 需求个数",         "测试设计", "个",  "int",     "sum",          None, None,                True,  True,  11),
    ("ai_req_coverage",       "AI 涉及需求覆盖率",   "测试设计", "%",   "percent", "computed",     "ai_req_count/req_count*100", None,        False, True,  12),
    ("new_case_count",        "新增用例个数",         "测试设计", "个",  "int",     "sum",          None, None,                True,  True,  13),
    ("ai_case_count",         "AI 用例个数",          "测试设计", "个",  "int",     "sum",          None, None,                True,  True,  14),
    ("ai_case_ratio",         "测试用例 AI 生成占比", "测试设计", "%",   "percent", "computed",     "ai_case_count/new_case_count*100", None, False, True,  15),
    ("ai_case_adoption_rate", "AI 生成用例采纳率",    "测试设计", "%",   "percent", "weighted_avg", "ai_case_adopted/ai_case_count*100", "ai_case_count", False, True, 16),
    # ── 测试脚本生成 ──
    ("ai_script_code_ratio",  "AI 生成脚本代码占比", "测试脚本生成", "%",   "percent", "computed",     "ai_code_lines/total_code_lines*100", None, False, True,  20),
    ("ai_code_lines",         "AI 生成代码入库行数", "测试脚本生成", "行",  "int",     "sum",          None, None,                True,  True,  21),
    ("ai_code_accuracy",      "AI 生成代码准确率",   "测试脚本生成", "%",   "percent", "weighted_avg", "ai_code_accurate_lines/ai_code_lines*100", "ai_code_lines", False, True, 22),
    ("new_script_ai_ratio",   "新增脚本 AI 辅助占比", "测试脚本生成", "%",   "percent", "weighted_avg", "new_script_ai_assisted_count/new_script_count*100", "new_script_count", False, True, 23),
]


ATOMIC_VALUE_RANGES = {
    # code: (low, high) 每天每领域 per-project
    "req_count":          (3, 18),
    "ai_req_count":       (0, 12),         # ≤ req_count
    "new_case_count":     (10, 50),
    "ai_case_count":      (3, 35),         # ≤ new_case_count
    "ai_case_adopted":    (0, 30),         # ≤ ai_case_count
    "total_code_lines":   (200, 1200),
    "ai_code_lines":      (50, 800),
    "ai_code_accurate_lines": (40, 750),   # ≤ ai_code_lines
    "new_script_count":   (1, 8),
    "new_script_ai_assisted_count": (0, 6),
}


def seed_dimensions(db: Session):
    if not db.query(DimProject).count():
        for code, name in PROJECTS:
            db.add(DimProject(code=code, name=name))
    if not db.query(DimDomain).count():
        for code, name, sort_order in DOMAINS:
            db.add(DimDomain(code=code, name=name, sort_order=sort_order))
    if not db.query(MetricDef).count():
        for (code, label, cat, unit, dt, agg, formula, weight, drill, vis, so) in METRIC_DEFS:
            db.add(MetricDef(
                code=code, label=label, category=cat, unit=unit, data_type=dt,
                agg_method=agg, computed_formula=formula, weight_metric=weight,
                drilldown_enabled=drill, is_default_visible=vis, sort_order=so,
            ))
    if not db.query(ViewTemplate).count():
        for vt in VIEW_TEMPLATES:
            db.add(ViewTemplate(**vt))
    db.commit()


def seed_facts(db: Session, days: int):
    today = date.today()
    detail_id = 1
    for d_offset in range(days):
        the_date = today - timedelta(days=d_offset)
        for proj, _ in PROJECTS:
            for domain, _, _ in DOMAINS:
                req = random.randint(*ATOMIC_VALUE_RANGES["req_count"])
                ai_req = random.randint(0, req)
                new_case = random.randint(*ATOMIC_VALUE_RANGES["new_case_count"])
                ai_case = random.randint(0, new_case)
                ai_case_adopted = random.randint(0, ai_case)
                total_lines = random.randint(*ATOMIC_VALUE_RANGES["total_code_lines"])
                ai_lines = random.randint(0, total_lines)
                ai_accurate = random.randint(int(ai_lines * 0.85), ai_lines) if ai_lines else 0
                new_script = random.randint(*ATOMIC_VALUE_RANGES["new_script_count"])
                new_script_ai = random.randint(0, new_script)

                vals = {
                    "req_count": req, "ai_req_count": ai_req,
                    "new_case_count": new_case, "ai_case_count": ai_case, "ai_case_adopted": ai_case_adopted,
                    "total_code_lines": total_lines, "ai_code_lines": ai_lines,
                    "ai_code_accurate_lines": ai_accurate,
                    "new_script_count": new_script, "new_script_ai_assisted_count": new_script_ai,
                }
                for mcode, mval in vals.items():
                    db.merge(AiMetric(
                        period_date=the_date, project_code=proj, domain_code=domain,
                        iteration_code=None, org_path=None,
                        metric_code=mcode, metric_value=float(mval), source="seed",
                    ))

                # 部分领域当天给一些明细（便于演示 drilldown）
                if d_offset < 3:
                    for i in range(min(ai_case, 5)):
                        db.add(AiMetricDetail(
                            period_date=the_date, project_code=proj, domain_code=domain,
                            metric_code="ai_case_count", detail_type="case",
                            ref_id=f"CASE-{proj}-{domain}-{the_date}-{i}",
                            ref_label=f"AI 用例 #{i+1} for {domain}",
                            ref_url=f"https://testrail.example.com/case/{detail_id}",
                            is_ai_generated=True,
                            is_adopted=(i < ai_case_adopted // max(1, ai_case // 5 or 1)) if ai_case else None,
                            payload={"source_tool": "AI Designer"},
                        ))
                        detail_id += 1
                    if ai_lines:
                        # AI 入库代码明细按 PR 粒度
                        for i in range(min(3, max(1, ai_lines // 200))):
                            db.add(AiMetricDetail(
                                period_date=the_date, project_code=proj, domain_code=domain,
                                metric_code="ai_code_lines", detail_type="code_change",
                                ref_id=f"MR-{proj}-{the_date}-{detail_id}",
                                ref_label=f"feat: AI 生成自动化脚本 (#{detail_id})",
                                ref_url=f"https://gitlab.example.com/!{detail_id}",
                                is_ai_generated=True,
                                payload={"lines": ai_lines // 3, "files": random.randint(1, 5)},
                            ))
                            detail_id += 1
    db.commit()


def reset_facts(db: Session):
    db.execute(delete(AiMetric))
    db.execute(delete(AiMetricDetail))
    db.commit()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--reset", action="store_true")
    args = ap.parse_args()
    init_db()
    with SessionLocal() as db:
        seed_dimensions(db)
        if args.reset:
            reset_facts(db)
        seed_facts(db, args.days)
        print(f"seeded {args.days} days × {len(PROJECTS)} projects × {len(DOMAINS)} domains")


if __name__ == "__main__":
    main()
