"""指标查询 + 多版本拣选 + 聚合 + computed 计算 + 行级 filter/sort/page。

V2 升级要点：
- 每个 (period_date, source) 可有多个 version_no；查询默认取 latest_valid
  （即不在 ai_metric_invalid_mark 表中的最大 version_no）
- row_dim 支持 'domain' (默认 6 行) / 'project>domain' (默认依映射)
- 行级筛选 (row_filter)：对衍生后的 row dict 应用谓词
- 行级排序 (sort)：支持任意字段、收藏优先
- 行级分页 (page, page_size)：在 Python 层切片，total 反映过滤后的行数
- 行收藏：基于 user_row_favorite 表 join；查询时给每行 _row_favorite=true/false

V4 升级要点：
- ATOMIC_METRICS 与 computed 列**改为运行时从 `metric_def` 读取**：
  - atomic = metric_def 中 agg_method='sum' 且 is_active=True 的 code
  - computed = agg_method in ('computed','weighted_avg') 且 formula 形如 'A/B*100'
- 数据库空（启动 seed 前）时 fallback 到 DEFAULT_ATOMIC（避免 init_db 后裸跑就 500）
"""
from __future__ import annotations
import json
import re
from datetime import date, timedelta
from collections import defaultdict
from typing import Literal

from sqlalchemy import select, func, and_
from sqlalchemy.orm import Session

from ..models import (
    AiMetric, AiMetricDetail, AiMetricInvalidMark,
    DimDomain, DimProject, MetricDef, UserRowFavorite, ReportFactDaily,
)
from ..settings import settings


def _pct(num, den):
    if not den:
        return None
    return round(float(num) * 100.0 / float(den), 2)


# 启动 fallback：当 metric_def 为空（init_db 之后、seed 之前）时使用
DEFAULT_ATOMIC = [
    "req_count", "ai_req_count",
    "new_case_count", "ai_case_count",
    "ai_case_adopted",
    "ai_code_lines", "total_code_lines",
    "ai_code_accurate_lines",
    "new_script_count", "new_script_ai_assisted_count",
]


# 形如 'ai_req_count/req_count*100' 的简单百分比公式
# computed/weighted_avg 行级展开都退化成此形式（更高级公式后续 V5 再做）
_PCT_FORMULA_RE = re.compile(r"^\s*([a-z_][a-z0-9_]*)\s*/\s*([a-z_][a-z0-9_]*)\s*\*\s*100\s*$")


def _parse_pct_formula(s: str | None) -> tuple[str, str] | None:
    if not s:
        return None
    m = _PCT_FORMULA_RE.match(s)
    return (m.group(1), m.group(2)) if m else None


def get_atomic_metrics(db: Session) -> list[str]:
    """从 metric_def 拉 atomic 指标 code 列表（按 sort_order）。空表 fallback 到 DEFAULT_ATOMIC。"""
    rows = db.execute(
        select(MetricDef.code)
        .where(MetricDef.agg_method == "sum", MetricDef.is_active.is_(True))
        .order_by(MetricDef.sort_order, MetricDef.code)
    ).scalars().all()
    return list(rows) if rows else list(DEFAULT_ATOMIC)


def get_computed_specs(db: Session) -> list[tuple[str, str, str]]:
    """返回 [(out_code, num_code, den_code), ...]；formula 无法解析的 metric 会跳过。"""
    rows = db.execute(
        select(MetricDef.code, MetricDef.computed_formula)
        .where(MetricDef.agg_method.in_(["computed", "weighted_avg"]),
               MetricDef.is_active.is_(True))
        .order_by(MetricDef.sort_order, MetricDef.code)
    ).all()
    specs: list[tuple[str, str, str]] = []
    for code, formula in rows:
        parts = _parse_pct_formula(formula)
        if parts:
            specs.append((code, parts[0], parts[1]))
    return specs


# ────────────────────────────────────────────────────────────────────
# 多版本拣选：返回 set of (date, source, version_no) 表示"每日每来源 latest valid"
# ────────────────────────────────────────────────────────────────────

def latest_valid_versions(
    db: Session,
    date_from: date | None,
    date_to: date | None,
    sources: list[str] | None = None,
    version_pin: dict[tuple[date, str], int] | None = None,
) -> dict[tuple[date, str], int]:
    """计算 (date, source) → version_no 字典。

    - 默认：未被 invalid 标记的 MAX(version_no)
    - 若调用方传 version_pin={(date, source): version_no}，强制使用指定版本（即便已被标 invalid）
    - MySQL 5.7 兼容：GROUP BY + MAX，不使用 CTE/窗口函数
    """
    conds = []
    if date_from: conds.append(AiMetric.period_date >= date_from)
    if date_to:   conds.append(AiMetric.period_date <= date_to)
    if sources:   conds.append(AiMetric.source.in_(sources))

    # LEFT JOIN invalid_mark，过滤掉已标 invalid 的版本
    stmt = (
        select(
            AiMetric.period_date,
            AiMetric.source,
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
        .where(and_(AiMetricInvalidMark.id.is_(None), *conds))
        .group_by(AiMetric.period_date, AiMetric.source)
    )
    out = {(d, s): int(v) for d, s, v in db.execute(stmt).all()}

    if version_pin:
        out.update(version_pin)
    return out


def list_versions(
    db: Session,
    date_from: date | None,
    date_to: date | None,
    sources: list[str] | None = None,
) -> list[dict]:
    """返回 [{date, source, version_no, is_valid, marked_at?, reason?}] 给前端 versions 切换器。"""
    conds = []
    if date_from: conds.append(AiMetric.period_date >= date_from)
    if date_to:   conds.append(AiMetric.period_date <= date_to)
    if sources:   conds.append(AiMetric.source.in_(sources))
    stmt = (
        select(AiMetric.period_date, AiMetric.source, AiMetric.version_no)
        .where(and_(True, *conds))
        .group_by(AiMetric.period_date, AiMetric.source, AiMetric.version_no)
        .order_by(AiMetric.period_date.desc(), AiMetric.source.asc(), AiMetric.version_no.desc())
    )
    versions = db.execute(stmt).all()

    # 同时拉 invalid_mark 加注
    mc = []
    if date_from: mc.append(AiMetricInvalidMark.period_date >= date_from)
    if date_to:   mc.append(AiMetricInvalidMark.period_date <= date_to)
    if sources:   mc.append(AiMetricInvalidMark.source.in_(sources))
    mark_stmt = select(AiMetricInvalidMark)
    if mc: mark_stmt = mark_stmt.where(and_(*mc))
    marks = {
        (m.period_date, m.source, m.version_no): m
        for m in db.execute(mark_stmt).scalars().all()
    }

    out = []
    for d, s, v in versions:
        mk = marks.get((d, s, v))
        out.append({
            "period_date": d.isoformat(),
            "source": s,
            "version_no": v,
            "is_valid": mk is None,
            "marked_at": mk.marked_at.isoformat() if mk else None,
            "marked_by": mk.marked_by if mk else None,
            "reason": mk.reason if mk else None,
        })
    return out


# ────────────────────────────────────────────────────────────────────
# 汇总查询 (主 entry)
# ────────────────────────────────────────────────────────────────────

def _row_key_str(row_dim: str, vals: dict) -> str:
    """row_key 规范化为字符串（行收藏 / 前端引用都用它）。"""
    if row_dim == "domain":
        return f"domain_code={vals['domain_code']}"
    if row_dim == "project>domain":
        return f"project_code={vals['project_code']};domain_code={vals['domain_code']}"
    raise ValueError(f"unsupported row_dim: {row_dim}")


def _derive_computed(r: dict, specs: list[tuple[str, str, str]] | None = None) -> None:
    """填充 computed/weighted_avg 列。原地改 r。

    specs: 调用方传入；未传则保守 fallback 到内建（兼容旧调用）。
    """
    if specs is None:
        specs = [
            ("ai_req_coverage",       "ai_req_count",                  "req_count"),
            ("ai_case_ratio",         "ai_case_count",                 "new_case_count"),
            ("ai_case_adoption_rate", "ai_case_adopted",               "ai_case_count"),
            ("ai_script_code_ratio",  "ai_code_lines",                 "total_code_lines"),
            ("ai_code_accuracy",      "ai_code_accurate_lines",        "ai_code_lines"),
            ("new_script_ai_ratio",   "new_script_ai_assisted_count",  "new_script_count"),
        ]
    for out, num, den in specs:
        r[out] = _pct(r.get(num), r.get(den))


def _apply_row_filter(rows: list[dict], filters: list[dict]) -> list[dict]:
    """row_filter: [{column, op, value}, ...]; op ∈ {eq, ne, gt, gte, lt, lte, in, ilike}"""
    if not filters: return rows
    def match(r, f):
        v = r.get(f["column"])
        op = f["op"]; val = f["value"]
        if v is None: return False
        if op == "eq":   return v == val
        if op == "ne":   return v != val
        if op == "gt":   return v > val
        if op == "gte":  return v >= val
        if op == "lt":   return v < val
        if op == "lte":  return v <= val
        if op == "in":   return v in (val if isinstance(val, list) else [val])
        if op == "ilike": return val.lower() in str(v).lower()
        return False
    return [r for r in rows if all(match(r, f) for f in filters)]


def _apply_sort(rows: list[dict], sort: str | None, row_favorite_first: bool = True) -> list[dict]:
    """sort='field:dir' 或多段 'field:dir,field:dir'；收藏优先恒前置。"""
    sorters = []
    if row_favorite_first:
        sorters.append(("_row_favorite", "desc"))
    if sort:
        for term in sort.split(","):
            if ":" in term:
                f, d = term.split(":")
                sorters.append((f.strip(), d.strip().lower()))
            elif term.strip():
                sorters.append((term.strip(), "asc"))
    if not sorters: return rows
    def keyf(r):
        out = []
        for f, _ in sorters:
            v = r.get(f)
            if v is None:
                # None 视为最小值（升序时排末尾）→ 用 (1, 0) sentinel
                out.append((1, 0))
            else:
                out.append((0, v))
        return tuple(out)
    # multi-key sort: 依次稳定排序
    for f, d in reversed(sorters):
        rows = sorted(rows, key=lambda r: (
            (1, 0) if r.get(f) is None else (0, r.get(f))
        ), reverse=(d == "desc"))
    return rows


def _shift_range(date_from: date, date_to: date, policy: str) -> tuple[date, date]:
    """计算对比期日期区间。

    - prev_period: 相同长度的紧邻前一段 (业务上的"环比")
    - prev_month : (date_from - 1 month, date_to - 1 month) 近似（按 30 天）
    - prev_year  : (date_from - 365 days, date_to - 365 days)
    """
    days = (date_to - date_from).days + 1
    if policy == "prev_period":
        return date_from - timedelta(days=days), date_from - timedelta(days=1)
    if policy == "prev_month":
        return date_from - timedelta(days=30), date_to - timedelta(days=30)
    if policy == "prev_year":
        return date_from - timedelta(days=365), date_to - timedelta(days=365)
    raise ValueError(f"unsupported compare_with: {policy}")


def _delta_pct(now, prev):
    if prev is None or prev == 0: return None
    if now is None: return None
    return round((float(now) - float(prev)) * 100.0 / float(prev), 2)


def fetch_summary(
    db: Session,
    date_from: date | None,
    date_to: date | None,
    project_codes: list[str] | None,
    row_dim: str = "domain",          # "domain" | "project>domain"
    user_id: str = "anon",
    row_filter: list[dict] | None = None,
    sort: str | None = None,
    page: int = 1, page_size: int = 200,
    paging_mode: str = "server",      # "server" | "none"
    version_pin: dict | None = None,
    compare_with: str | None = None,
) -> dict:
    """主接口：返回 {items, totals, page, page_size, total, version_summary}。"""
    if row_dim not in ("domain", "project>domain"):
        raise ValueError(f"row_dim={row_dim} 不支持；可选 'domain' / 'project>domain'")

    # V4: 运行时读 metric_def，原子指标 + computed 公式都数据驱动
    atomic_metrics = get_atomic_metrics(db)
    computed_specs = get_computed_specs(db)

    # 1) 拣 latest valid version（受 invalid_mark 影响）
    pin = None
    if version_pin:
        pin = {(v["period_date"] if isinstance(v["period_date"], date) else date.fromisoformat(v["period_date"]),
                v["source"]): int(v["version_no"]) for v in version_pin}
    valid = latest_valid_versions(db, date_from, date_to, version_pin=pin)
    if not valid:
        return {"rows": [], "totals": _empty_totals(row_dim, atomic_metrics, computed_specs), "total": 0, "valid_versions": []}

    # 2) 把 (date, source, version) 三元组 OR 合并成 WHERE 子句
    #    MySQL 不能直接 IN tuple，所以用 OR 列表 + UNION ALL alternative
    #    实现：先 LEFT JOIN invalid_mark, 取 NOT EXISTS 的，然后 GROUP BY date,source MAX(ver)
    #    再 INNER JOIN 回 fact 表
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
    )
    if date_from: valid_sq = valid_sq.where(AiMetric.period_date >= date_from)
    if date_to:   valid_sq = valid_sq.where(AiMetric.period_date <= date_to)
    valid_sq = valid_sq.group_by(AiMetric.period_date, AiMetric.source).subquery("vv")

    conds = [AiMetric.metric_code.in_(atomic_metrics)]
    if date_from: conds.append(AiMetric.period_date >= date_from)
    if date_to:   conds.append(AiMetric.period_date <= date_to)
    if project_codes: conds.append(AiMetric.project_code.in_(project_codes))

    # 3) 主查询：根据 settings.use_preagg 决定走 long-table+latest_valid join 还是预聚合表
    if settings.use_preagg:
        if row_dim == "domain":
            pa_cols = [ReportFactDaily.domain_code]
        else:
            pa_cols = [ReportFactDaily.project_code, ReportFactDaily.domain_code]
        pa_conds = [ReportFactDaily.metric_code.in_(atomic_metrics)]
        if date_from: pa_conds.append(ReportFactDaily.period_date >= date_from)
        if date_to:   pa_conds.append(ReportFactDaily.period_date <= date_to)
        if project_codes: pa_conds.append(ReportFactDaily.project_code.in_(project_codes))
        stmt = (
            select(*pa_cols, ReportFactDaily.metric_code,
                   func.sum(ReportFactDaily.metric_value).label("v"))
            .where(and_(True, *pa_conds))
            .group_by(*pa_cols, ReportFactDaily.metric_code)
        )
        rows_raw = db.execute(stmt).all()
    else:
        if row_dim == "domain":
            group_cols = [AiMetric.domain_code]
        else:
            group_cols = [AiMetric.project_code, AiMetric.domain_code]
        stmt = (
            select(*group_cols, AiMetric.metric_code,
                   func.sum(AiMetric.metric_value).label("v"))
            .join(valid_sq, and_(
                AiMetric.period_date == valid_sq.c.d,
                AiMetric.source == valid_sq.c.s,
                AiMetric.version_no == valid_sq.c.v,
            ))
            .where(and_(True, *conds))
            .group_by(*group_cols, AiMetric.metric_code)
        )
        rows_raw = db.execute(stmt).all()

    # 4) 装配 row dict
    keyfn = (lambda r: (r[0],)) if row_dim == "domain" else (lambda r: (r[0], r[1]))
    grouped: dict[tuple, dict] = defaultdict(lambda: {m: 0.0 for m in atomic_metrics})
    for row in rows_raw:
        if row_dim == "domain":
            key = (row[0],); m_code = row[1]; v = row[2]
        else:
            key = (row[0], row[1]); m_code = row[2]; v = row[3]
        grouped[key][m_code] = float(v) if v is not None else 0.0

    # 5) 拉显示名 & favorites
    dom_meta = {d.code: d for d in db.execute(select(DimDomain)).scalars()}
    proj_meta = {p.code: p for p in db.execute(select(DimProject)).scalars()}
    fav_keys = set(_load_row_favorites(db, user_id, "ai_metrics"))

    # 6) 衍生 + 排序 + 过滤
    items: list[dict] = []
    for key, atoms in grouped.items():
        r = dict(atoms)
        _derive_computed(r, computed_specs)
        if row_dim == "domain":
            domain_code = key[0]
            r["_domain_code_raw"] = domain_code
            r["domain_code"] = dom_meta[domain_code].name if domain_code in dom_meta else domain_code
            r["_row_key"] = {"domain_code": domain_code}
            r_key_str = _row_key_str("domain", {"domain_code": domain_code})
        else:
            project_code, domain_code = key
            r["_project_code_raw"] = project_code
            r["_domain_code_raw"] = domain_code
            r["project_code"] = proj_meta[project_code].name if project_code in proj_meta else project_code
            r["domain_code"] = dom_meta[domain_code].name if domain_code in dom_meta else domain_code
            r["_row_key"] = {"project_code": project_code, "domain_code": domain_code}
            r_key_str = _row_key_str("project>domain", {"project_code": project_code, "domain_code": domain_code})
        r["_row_favorite"] = r_key_str in fav_keys
        items.append(r)

    # 排序 (默认按主维度名)
    if row_dim == "domain":
        # 优先 domain 的 sort_order
        items.sort(key=lambda r: (dom_meta.get(r["_domain_code_raw"]).sort_order if r["_domain_code_raw"] in dom_meta else 99, r["_domain_code_raw"]))
    else:
        items.sort(key=lambda r: (r["_project_code_raw"], dom_meta.get(r["_domain_code_raw"]).sort_order if r["_domain_code_raw"] in dom_meta else 99))

    # 行级 filter
    items = _apply_row_filter(items, row_filter or [])
    # 用户排序（覆盖默认；收藏优先）
    items = _apply_sort(items, sort)

    total = len(items)

    # 7) 合计行：基于已过滤的全集再算一遍（不分页）
    totals_atom = {m: sum(it.get(m, 0) or 0 for it in items) for m in atomic_metrics}
    totals = dict(totals_atom)
    _derive_computed(totals, computed_specs)
    if row_dim == "domain":
        totals["domain_code"] = "合计"
    else:
        totals["project_code"] = "合计"
        totals["domain_code"] = ""
    totals["_is_total"] = True

    # 8) 分页
    if paging_mode == "server":
        start = (page - 1) * page_size
        items = items[start: start + page_size]

    # 9) compare: 把当前窗口"平移"再算一次（不再分页/排序），合并到响应
    compare_data = None
    if compare_with and compare_with != "none" and date_from and date_to:
        prev_from, prev_to = _shift_range(date_from, date_to, compare_with)
        # 递归调用自己，不带 sort / paging / compare（避免无限循环），row_dim 一致
        prev_resp = fetch_summary(
            db, date_from=prev_from, date_to=prev_to, project_codes=project_codes,
            row_dim=row_dim, user_id=user_id, row_filter=None,
            sort=None, page=1, page_size=10_000, paging_mode="none",
            version_pin=None, compare_with=None,
        )
        # 索引 prev rows 用主键映射
        prev_by_key: dict[str, dict] = {}
        for pr in prev_resp["rows"]:
            if row_dim == "domain":
                k = f"domain_code={pr['_domain_code_raw']}"
            else:
                k = f"project_code={pr['_project_code_raw']};domain_code={pr['_domain_code_raw']}"
            prev_by_key[k] = pr
        # 给当前 items 加 _prev_{m} / _delta_{m} / _delta_pct_{m}
        comparable_metrics = atomic_metrics + [s[0] for s in computed_specs]
        for it in items:
            if row_dim == "domain":
                k = f"domain_code={it['_domain_code_raw']}"
            else:
                k = f"project_code={it['_project_code_raw']};domain_code={it['_domain_code_raw']}"
            prev = prev_by_key.get(k, {})
            for m in comparable_metrics:
                pv = prev.get(m)
                it[f"_prev_{m}"] = pv
                it[f"_delta_pct_{m}"] = _delta_pct(it.get(m), pv)
        # 合计行的对比
        prev_totals = prev_resp["totals"]
        for m in comparable_metrics:
            pv = prev_totals.get(m)
            totals[f"_prev_{m}"] = pv
            totals[f"_delta_pct_{m}"] = _delta_pct(totals.get(m), pv)
        compare_data = {
            "policy": compare_with,
            "prev_from": prev_from.isoformat(),
            "prev_to": prev_to.isoformat(),
            "now_from": date_from.isoformat(),
            "now_to": date_to.isoformat(),
        }

    return {
        "rows": items,
        "totals": totals,
        "total": total,
        "valid_versions": _summarize_valid(valid),
        "compare": compare_data,
    }


def _empty_totals(row_dim, atomic_metrics=None, computed_specs=None):
    am = atomic_metrics if atomic_metrics is not None else DEFAULT_ATOMIC
    out = {m: 0.0 for m in am}
    _derive_computed(out, computed_specs)
    out["domain_code"] = "合计"
    if row_dim == "project>domain":
        out["project_code"] = "合计"; out["domain_code"] = ""
    out["_is_total"] = True
    return out


def _summarize_valid(valid: dict) -> dict:
    """生成"已选版本"摘要 给响应的 meta 部分。"""
    by_date: dict[str, dict[str, int]] = {}
    for (d, s), v in valid.items():
        by_date.setdefault(d.isoformat(), {})[s] = v
    return {"count": len(valid), "by_date": by_date}


# ────────────────────────────────────────────────────────────────────
# 列值去重
# ────────────────────────────────────────────────────────────────────

def fetch_distinct(
    db: Session, column: str,
    date_from: date | None, date_to: date | None,
    project_codes: list[str] | None,
) -> list[dict]:
    """列值去重（受 latest_valid 过滤）。"""
    if column not in ("domain_code", "project_code"):
        return []
    valid_sq = _build_valid_sq(date_from, date_to)

    target_col = AiMetric.domain_code if column == "domain_code" else AiMetric.project_code
    conds = []
    if date_from: conds.append(AiMetric.period_date >= date_from)
    if date_to:   conds.append(AiMetric.period_date <= date_to)
    if project_codes: conds.append(AiMetric.project_code.in_(project_codes))

    stmt = (
        select(target_col, func.count().label("c"))
        .join(valid_sq, and_(
            AiMetric.period_date == valid_sq.c.d,
            AiMetric.source == valid_sq.c.s,
            AiMetric.version_no == valid_sq.c.v,
        ))
        .where(and_(True, *conds))
        .group_by(target_col)
        .order_by(func.count().desc())
    )
    rows = db.execute(stmt).all()
    if column == "domain_code":
        names = {d.code: d.name for d in db.execute(select(DimDomain)).scalars()}
    else:
        names = {p.code: p.name for p in db.execute(select(DimProject)).scalars()}
    return [{"value": v, "label": names.get(v, v), "count": c} for v, c in rows]


def _build_valid_sq(date_from, date_to):
    sq = (
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
    )
    if date_from: sq = sq.where(AiMetric.period_date >= date_from)
    if date_to:   sq = sq.where(AiMetric.period_date <= date_to)
    return sq.group_by(AiMetric.period_date, AiMetric.source).subquery("vv")


# ────────────────────────────────────────────────────────────────────
# 下钻 (详情)
# ────────────────────────────────────────────────────────────────────

# metric_code → (canonical detail_type 用于 WHERE，is_ai 过滤可选)
# 例：用户点 "ai_req_count" → 查 detail_type='requirement' AND is_ai_generated=True
METRIC_DRILL_MAP: dict[str, dict] = {
    "req_count":           {"detail_type": "requirement"},                # all
    "ai_req_count":        {"detail_type": "requirement", "is_ai": True},
    "new_case_count":      {"detail_type": "case"},
    "ai_case_count":       {"detail_type": "case", "is_ai": True},
    "ai_case_adopted":     {"detail_type": "case", "is_ai": True, "adopted": True},
    "total_code_lines":    {"detail_type": "code_change"},
    "ai_code_lines":       {"detail_type": "code_change", "is_ai": True},
    "new_script_count":    {"detail_type": "script_file"},
    "new_script_ai_assisted_count": {"detail_type": "script_file", "is_ai": True},
    "ai_code_accurate_lines": {"detail_type": "code_review"},
}


def fetch_drilldown(
    db: Session,
    *,
    domain_code: str | None,
    project_code: str | None,
    metric_code: str,
    date_from: date | None, date_to: date | None,
    project_codes: list[str] | None,
    row_filter: list[dict] | None = None,
    sort: str | None = None,
    page: int = 1, page_size: int = 50,
) -> dict:
    """下钻：拉 ai_metric_detail；按 latest_valid 版本过滤；按 metric_code 映射到 detail_type + is_ai 过滤。"""
    valid_sq = _build_valid_sq(date_from, date_to)

    drill_spec = METRIC_DRILL_MAP.get(metric_code)
    if drill_spec:
        conds = [AiMetricDetail.detail_type == drill_spec["detail_type"]]
        if drill_spec.get("is_ai") is True:
            conds.append(AiMetricDetail.is_ai_generated.is_(True))
        if drill_spec.get("adopted") is True:
            conds.append(AiMetricDetail.is_adopted.is_(True))
    else:
        # 未注册的 metric_code：保守回退到精确匹配
        conds = [AiMetricDetail.metric_code == metric_code]

    if domain_code: conds.append(AiMetricDetail.domain_code == domain_code)
    if project_code: conds.append(AiMetricDetail.project_code == project_code)
    if date_from: conds.append(AiMetricDetail.period_date >= date_from)
    if date_to:   conds.append(AiMetricDetail.period_date <= date_to)
    if project_codes: conds.append(AiMetricDetail.project_code.in_(project_codes))

    # detail 表的 (date, source, version) 也得 join 到 valid_sq
    base = (
        select(AiMetricDetail)
        .join(valid_sq, and_(
            AiMetricDetail.period_date == valid_sq.c.d,
            AiMetricDetail.source == valid_sq.c.s,
            AiMetricDetail.version_no == valid_sq.c.v,
        ))
        .where(and_(True, *conds))
    )

    # 应用 row_filter (在 Python 层做更灵活)：先取所有，filter / sort / page
    all_rows = db.execute(base).scalars().all()
    items: list[dict] = []
    for r in all_rows:
        items.append({
            "ref_id": r.ref_id, "ref_label": r.ref_label, "ref_url": r.ref_url,
            "detail_type": r.detail_type, "metric_code": r.metric_code,
            "source": r.source, "version_no": r.version_no,
            "is_ai_generated": bool(r.is_ai_generated),
            "is_adopted": (None if r.is_adopted is None else bool(r.is_adopted)),
            "severity": r.severity, "author": r.author, "status": r.status,
            "period_date": r.period_date.isoformat(),
            "project_code": r.project_code, "domain_code": r.domain_code,
            "payload": r.payload,
        })

    items = _apply_row_filter(items, row_filter or [])
    items = _apply_sort(items, sort or "period_date:desc,ref_id:desc", row_favorite_first=False)

    total = len(items)
    start = (page - 1) * page_size
    paged = items[start: start + page_size]
    return {"items": paged, "page": page, "page_size": page_size,
            "total": total, "has_more": page * page_size < total}


# ────────────────────────────────────────────────────────────────────
# Row favorites
# ────────────────────────────────────────────────────────────────────

def _load_row_favorites(db: Session, user_id: str, report_type: str) -> list[str]:
    rows = db.execute(
        select(UserRowFavorite.row_key)
        .where(UserRowFavorite.user_id == user_id, UserRowFavorite.report_type == report_type)
    ).scalars().all()
    return list(rows)


def toggle_row_favorite(db: Session, user_id: str, report_type: str,
                        row_key: dict, favorited: bool) -> bool:
    # 用规范化 string 当 key
    if "project_code" in row_key:
        key_str = f"project_code={row_key['project_code']};domain_code={row_key.get('domain_code','')}"
    else:
        key_str = f"domain_code={row_key.get('domain_code','')}"
    existing = db.execute(
        select(UserRowFavorite).where(
            UserRowFavorite.user_id == user_id,
            UserRowFavorite.report_type == report_type,
            UserRowFavorite.row_key == key_str,
        )
    ).scalar_one_or_none()
    if favorited and not existing:
        db.add(UserRowFavorite(user_id=user_id, report_type=report_type, row_key=key_str))
    elif not favorited and existing:
        db.delete(existing)
    db.commit()
    return True


def mark_version_invalid(db: Session, period_date: date, source: str, version_no: int,
                         marked_by: str | None, reason: str | None) -> None:
    """标记某版本无效；下次查询自动跳过。"""
    existing = db.execute(
        select(AiMetricInvalidMark).where(
            AiMetricInvalidMark.period_date == period_date,
            AiMetricInvalidMark.source == source,
            AiMetricInvalidMark.version_no == version_no,
        )
    ).scalar_one_or_none()
    if existing:
        return
    db.add(AiMetricInvalidMark(
        period_date=period_date, source=source, version_no=version_no,
        marked_by=marked_by, reason=reason,
    ))
    db.commit()


def unmark_version(db: Session, period_date: date, source: str, version_no: int) -> None:
    existing = db.execute(
        select(AiMetricInvalidMark).where(
            AiMetricInvalidMark.period_date == period_date,
            AiMetricInvalidMark.source == source,
            AiMetricInvalidMark.version_no == version_no,
        )
    ).scalar_one_or_none()
    if existing:
        db.delete(existing); db.commit()
