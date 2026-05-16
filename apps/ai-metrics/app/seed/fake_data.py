"""**只灌维度** (dim_project / dim_domain / metric_def / view_template)。

事实表数据走 `python -m app.sources.simulate` —— 那里保证 metric_value = SUM(detail)，
内外一致。本脚本只负责"基础设施"。

运行：
    python -m app.seed.fake_data --reset      # 清掉维度并重建
    python -m app.seed.fake_data              # 幂等填充（已有则不重复）
"""
from __future__ import annotations
import argparse
import random
from datetime import date, timedelta

from sqlalchemy.orm import Session
from sqlalchemy import delete, select

from app.db import SessionLocal, init_db
from app.models import (
    DimProject, DimDomain, DimProjectDomain, MetricDef, AiMetric, AiMetricDetail, ViewTemplate
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

# 项目-领域映射（真实业务：每个项目只有一部分领域）
# 行：项目；列：是否包含该领域
PROJECT_DOMAINS = {
    "proj_alpha":  ["core", "business", "frontend", "data"],          # 内容平台
    "proj_beta":   ["core", "business", "platform", "infra"],         # 交易中台
    "proj_gamma":  ["core", "platform", "data", "infra"],             # 风控引擎
    "proj_delta":  ["core", "business", "platform", "data", "infra"], # 数据中台
}

# 指标定义（数据驱动，加指标改这里）
# 字段顺序: code, label, category, unit, data_type, agg_method, formula, weight, drill, visible, sort_order
METRIC_DEFS = [
    # ── 测试设计 ──
    ("req_count",             "需求个数",           "测试设计", "个",  "int",     "sum",          None, None,                True,  True,  10),
    ("ai_req_count",          "AI 需求个数",         "测试设计", "个",  "int",     "sum",          None, None,                True,  True,  11),
    ("ai_req_coverage",       "AI 涉及需求覆盖率",   "测试设计", "%",   "percent", "computed",     "ai_req_count/req_count*100", None,        False, True,  12),
    ("new_case_count",        "新增用例个数",         "测试设计", "个",  "int",     "sum",          None, None,                True,  True,  13),
    ("ai_case_count",         "AI 用例个数",          "测试设计", "个",  "int",     "sum",          None, None,                True,  True,  14),
    # 原子分母：用作 ai_case_adoption_rate 分子，不在 UI 默认展示
    ("ai_case_adopted",       "AI 用例已采纳数",     "测试设计", "个",  "int",     "sum",          None, None,                True,  False, 15),
    ("ai_case_ratio",         "测试用例 AI 生成占比", "测试设计", "%",   "percent", "computed",     "ai_case_count/new_case_count*100", None, False, True,  16),
    ("ai_case_adoption_rate", "AI 生成用例采纳率",    "测试设计", "%",   "percent", "weighted_avg", "ai_case_adopted/ai_case_count*100", "ai_case_count", False, True, 17),
    # ── 测试脚本生成 ──
    ("ai_script_code_ratio",  "AI 生成脚本代码占比", "测试脚本生成", "%",   "percent", "computed",     "ai_code_lines/total_code_lines*100", None, False, True,  20),
    ("ai_code_lines",         "AI 生成代码入库行数", "测试脚本生成", "行",  "int",     "sum",          None, None,                True,  True,  21),
    # 原子分母：total_code_lines / ai_code_accurate_lines / new_script_ai_assisted_count
    ("total_code_lines",      "代码总入库行数",       "测试脚本生成", "行",  "int",     "sum",          None, None,                False, False, 24),
    ("ai_code_accurate_lines","AI 代码准确入库行数",  "测试脚本生成", "行",  "int",     "sum",          None, None,                False, False, 25),
    ("new_script_count",      "新增脚本数",          "测试脚本生成", "个",  "int",     "sum",          None, None,                False, False, 26),
    ("new_script_ai_assisted_count","AI 辅助新脚本数","测试脚本生成", "个",  "int",     "sum",          None, None,                False, False, 27),
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
    """V4: 逐行 upsert（按 code 检查），允许后续在源码加新维度直接生效。

    不会覆盖用户在 admin 后台修改过的 label/sort_order — 只会补齐缺失的 code。
    """
    existing_projects = {p.code for p in db.execute(select(DimProject)).scalars()}
    for code, name in PROJECTS:
        if code not in existing_projects:
            db.add(DimProject(code=code, name=name))

    existing_domains = {d.code for d in db.execute(select(DimDomain)).scalars()}
    for code, name, sort_order in DOMAINS:
        if code not in existing_domains:
            db.add(DimDomain(code=code, name=name, sort_order=sort_order))

    existing_pd = {(r.project_code, r.domain_code)
                   for r in db.execute(select(DimProjectDomain)).scalars()}
    for proj, doms in PROJECT_DOMAINS.items():
        for sort_idx, dom in enumerate(doms):
            if (proj, dom) not in existing_pd:
                db.add(DimProjectDomain(project_code=proj, domain_code=dom,
                                        is_active=True, sort_order=sort_idx))

    existing_metrics = {m.code for m in db.execute(select(MetricDef)).scalars()}
    for (code, label, cat, unit, dt, agg, formula, weight, drill, vis, so) in METRIC_DEFS:
        if code not in existing_metrics:
            db.add(MetricDef(
                code=code, label=label, category=cat, unit=unit, data_type=dt,
                agg_method=agg, computed_formula=formula, weight_metric=weight,
                drilldown_enabled=drill, is_default_visible=vis, sort_order=so,
            ))

    if not db.query(ViewTemplate).count():
        for vt in VIEW_TEMPLATES:
            db.add(ViewTemplate(**vt))
    db.commit()


def reset_facts(db: Session):
    db.execute(delete(AiMetric))
    db.execute(delete(AiMetricDetail))
    db.commit()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=0, help="V3: ignored — use `simulate` for facts")
    ap.add_argument("--reset", action="store_true", help="reset facts (清空 ai_metric + details)")
    args = ap.parse_args()
    init_db()
    with SessionLocal() as db:
        seed_dimensions(db)
        if args.reset:
            reset_facts(db)
        print(f"seeded dimensions: {len(PROJECTS)} projects × {len(DOMAINS)} domains × {len(METRIC_DEFS)} metrics × {len(VIEW_TEMPLATES)} view templates")
        print("== 接下来跑 `python -m app.sources.simulate --source all --days 30 --runs-per-day 2`")


if __name__ == "__main__":
    main()
