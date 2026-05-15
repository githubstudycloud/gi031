"""指标查询 + 聚合 + computed 计算。

实现要点：
- 用一个 GROUP BY domain_code 的查询取出所有 sum-able 指标
- computed (覆盖率/占比) 在应用层算（避免不同 DB 方言差异 + 除零保护）
- weighted_avg 需要额外存"分子"列；本期约定：采纳率 / 准确率 / 新脚本AI占比
  上报时存"分子"指标，分母是另一个 sum 指标，应用层除
- 合计行 = 同样的 SQL 不带 GROUP BY；computed/weighted 再算一次
"""
from datetime import date
from collections import defaultdict
from sqlalchemy import select, func, and_
from sqlalchemy.orm import Session

from ..models import AiMetric, AiMetricDetail, MetricDef, DimDomain


def _pct(num, den):
    if not den:
        return None
    return round(float(num) * 100.0 / float(den), 2)


# 聚合所需的所有"原子"指标 code （加 metric 时只改这表 + metric_def）
ATOMIC_METRICS = [
    "req_count", "ai_req_count",
    "new_case_count", "ai_case_count",
    "ai_case_adopted",            # 加权分子：已采纳的 AI 用例数
    "ai_code_lines", "total_code_lines",
    "ai_code_accurate_lines",     # 加权分子：AI 生成且无 bug 的入库行数
    "new_script_count", "new_script_ai_assisted_count",  # 加权分子
]


def fetch_summary(
    db: Session,
    date_from: date | None,
    date_to: date | None,
    project_codes: list[str] | None,
    row_dim: str = "domain",
) -> dict:
    """返回 {rows: [...], totals: {...}}。
    row_dim='domain' (V1)；后续 'project>domain' / 'org>domain' 可扩展。
    """
    if row_dim != "domain":
        raise ValueError(f"row_dim={row_dim} not supported yet")

    # 1. 取所有原子指标，按 (domain, metric) 聚合
    conds = [AiMetric.metric_code.in_(ATOMIC_METRICS)]
    if date_from: conds.append(AiMetric.period_date >= date_from)
    if date_to:   conds.append(AiMetric.period_date <= date_to)
    if project_codes:
        conds.append(AiMetric.project_code.in_(project_codes))

    q = (
        select(AiMetric.domain_code, AiMetric.metric_code, func.sum(AiMetric.metric_value).label("v"))
        .where(and_(*conds))
        .group_by(AiMetric.domain_code, AiMetric.metric_code)
    )
    raw = db.execute(q).all()

    # 组装为 {domain: {metric: value}}
    by_domain: dict[str, dict[str, float]] = defaultdict(lambda: {m: 0.0 for m in ATOMIC_METRICS})
    for d, m, v in raw:
        by_domain[d][m] = float(v) if v is not None else 0.0

    # 2. 取领域名（display label）
    domain_meta = {d.code: d for d in db.execute(select(DimDomain).order_by(DimDomain.sort_order, DimDomain.code)).scalars()}

    # 3. 算每行的 computed 列
    def derive(row: dict) -> dict:
        out = dict(row)
        out["ai_req_coverage"]      = _pct(row["ai_req_count"], row["req_count"])
        out["ai_case_ratio"]        = _pct(row["ai_case_count"], row["new_case_count"])
        out["ai_case_adoption_rate"] = _pct(row["ai_case_adopted"], row["ai_case_count"])
        out["ai_script_code_ratio"] = _pct(row["ai_code_lines"], row["total_code_lines"])
        out["ai_code_accuracy"]     = _pct(row["ai_code_accurate_lines"], row["ai_code_lines"])
        out["new_script_ai_ratio"]  = _pct(row["new_script_ai_assisted_count"], row["new_script_count"])
        return out

    rows = []
    for code, vals in sorted(by_domain.items(), key=lambda kv: (domain_meta.get(kv[0]).sort_order if kv[0] in domain_meta else 99, kv[0])):
        meta = domain_meta.get(code)
        r = derive(vals)
        r["domain_code"] = (meta.name if meta else code)
        r["_row_key"] = {"domain_code": code}
        r["_domain_code_raw"] = code
        rows.append(r)

    # 4. 合计行 (totals)：原子指标 sum，再走 derive
    totals_atom = {m: sum(d[m] for d in by_domain.values()) for m in ATOMIC_METRICS}
    totals = derive(totals_atom)
    totals["domain_code"] = "合计"
    totals["_is_total"] = True

    return {"rows": rows, "totals": totals}


def fetch_distinct(
    db: Session, column: str,
    date_from: date | None, date_to: date | None,
    project_codes: list[str] | None,
) -> list[dict]:
    """列值去重：仅 dim 列支持。"""
    if column == "domain_code":
        # 取 fact 表里实际出现过的领域 (受时间/项目筛选)
        conds = []
        if date_from: conds.append(AiMetric.period_date >= date_from)
        if date_to:   conds.append(AiMetric.period_date <= date_to)
        if project_codes:
            conds.append(AiMetric.project_code.in_(project_codes))
        q = (select(AiMetric.domain_code, func.count().label("c"))
             .where(and_(*conds)) if conds else select(AiMetric.domain_code, func.count().label("c")))
        q = q.group_by(AiMetric.domain_code).order_by(func.count().desc())
        rows = db.execute(q).all()
        domain_names = {d.code: d.name for d in db.execute(select(DimDomain)).scalars()}
        return [{"value": d, "label": domain_names.get(d, d), "count": c} for d, c in rows]
    return []


def fetch_drilldown(
    db: Session,
    domain_code: str,
    metric_code: str,
    date_from: date | None, date_to: date | None,
    project_codes: list[str] | None,
    page: int, page_size: int,
) -> dict:
    """下钻：返回 ai_metric_detail 行。"""
    conds = [AiMetricDetail.domain_code == domain_code,
             AiMetricDetail.metric_code == metric_code]
    if date_from: conds.append(AiMetricDetail.period_date >= date_from)
    if date_to:   conds.append(AiMetricDetail.period_date <= date_to)
    if project_codes:
        conds.append(AiMetricDetail.project_code.in_(project_codes))
    base = select(AiMetricDetail).where(and_(*conds)).order_by(AiMetricDetail.period_date.desc(), AiMetricDetail.id.desc())
    total = db.execute(select(func.count()).select_from(base.subquery())).scalar() or 0
    rows = db.execute(base.offset((page - 1) * page_size).limit(page_size)).scalars().all()
    items = [{
        "ref_id": r.ref_id, "ref_label": r.ref_label, "ref_url": r.ref_url,
        "detail_type": r.detail_type, "is_ai_generated": bool(r.is_ai_generated),
        "is_adopted": (None if r.is_adopted is None else bool(r.is_adopted)),
        "period_date": r.period_date.isoformat(),
        "payload": r.payload,
    } for r in rows]
    return {"items": items, "page": page, "page_size": page_size, "total": total,
            "has_more": page * page_size < total}
