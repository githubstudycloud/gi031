"""把 metric_def + dim_* 装配成报表平台协议的 /config 响应。

新增指标 / 改分组 / 加列只需要改数据库，**前端无需发版**。
"""
from sqlalchemy.orm import Session
from sqlalchemy import select

from ..models import MetricDef, DimDomain


CONFIG_VERSION = 2   # V2: row_dim_options + version mgmt + row_favorite


def assemble_config(db: Session, report_type: str = "ai_metrics") -> dict:
    metrics = db.execute(select(MetricDef).order_by(MetricDef.sort_order, MetricDef.code)).scalars().all()

    # 按 category 分组成 header_tree（领域列单独一组）
    by_cat: dict[str, list[MetricDef]] = {}
    for m in metrics:
        by_cat.setdefault(m.category, []).append(m)

    # 维度组：根据 row_dim 决定是 1 列还是 2 列；前端按 row_dim 选用
    # 我们在 header_tree 里固定声明两种维度列，前端按 row_dim 决定隐藏哪个
    dim_group = {
        "code": "_dim", "label": "维度",
        "children": [
            {
                "code": "project_code", "label": "项目", "data_type": "string",
                "is_default_visible": False, "default_order": 0, "default_pinned": "left",
                "default_width": 160, "sortable": True, "row_filterable": True,
                "display": {"kind": "text"},
                "visible_when": {"row_dim": ["project>domain"]}
            },
            {
                "code": "domain_code", "label": "领域", "data_type": "string",
                "is_default_visible": True, "default_order": 1, "default_pinned": "left",
                "default_width": 180, "sortable": True, "row_filterable": True,
                "display": {"kind": "text"}
            }
        ]
    }

    def leaf(m: MetricDef, idx: int) -> dict:
        node: dict = {
            "code": m.code, "label": m.label, "data_type": m.data_type,
            "is_default_visible": m.is_default_visible,
            "default_order": (m.sort_order or 0) + 10 + idx,
            "sortable": True, "row_filterable": True,
        }
        if m.unit == "%":
            node["display"] = {"kind": "percent", "precision": 1}
        elif m.unit == "行":
            node["display"] = {"kind": "number", "thousand": True}
        elif m.unit == "个":
            node["display"] = {"kind": "number"}
        else:
            node["display"] = {"kind": "number", "thousand": True}
        if m.drilldown_enabled:
            node["drilldown"] = {"ref": "metric_drill"}
        return node

    metric_groups = []
    for cat, ms in by_cat.items():
        metric_groups.append({
            "code": "_" + cat.replace(" ", "_"),
            "label": cat,
            "children": [leaf(m, i) for i, m in enumerate(ms)]
        })

    header_tree = [dim_group] + metric_groups

    return {
        "meta": {
            "report_type": report_type,
            "name": "AI 测试度量看板",
            "version": CONFIG_VERSION,
        },
        "primary_keys": ["domain_code"],
        "version": {
            "endpoint": f"/api/reports/{report_type}/versions",
            "param": "version", "default": "latest", "policy": "latest_per_day"
        },
        "filters": [
            {
                "code": "business_date", "label": "时间段", "kind": "date_range", "required": True,
                "default": {"preset": "last_30_days"},
                "param": {"from": "date_from", "to": "date_to"}
            },
            {
                "code": "projects", "label": "项目 (多选)", "kind": "multi_select",
                "required": True, "param": {"value": "project_codes"},
                "source": {
                    "endpoint": "/api/dropdowns/projects",
                    "paging": {"enabled": True, "page_size": 50},
                    "supports_favorite": True, "max_picks": 50,
                    "sortable_by": ["label", "is_favorite"],
                    "default_sort": [{"field": "is_favorite", "dir": "desc"}, {"field": "label", "dir": "asc"}],
                    "params_in": ["q", "page", "page_size", "sort", "only_favorites"]
                }
            }
        ],
        "primary_view": {
            "endpoint": f"/api/reports/{report_type}/summary",
            "method": "POST",
            "sortable": True,
            "paging": {"enabled": True, "default_page_size": 50, "default_mode": "server"},
            "row_favorite": {
                "enabled": True,
                "toggle_endpoint": f"/api/users/me/row_favorites/{report_type}",
                "sort_on_top": True
            },
            "row_totals": True,
            "row_dim_options": [
                {"code": "domain",         "label": "领域 (默认)",  "row_count_hint": 6},
                {"code": "project>domain", "label": "项目 + 领域",    "row_count_hint": 24}
            ],
            "default_row_dim": "domain",
            "row_filter": {"enabled": True}
        },
        "versions": {
            "list_endpoint": f"/api/reports/{report_type}/versions",
            "mark_endpoint": f"/api/reports/{report_type}/versions/mark",
            "policy": "latest_valid_per_date_source"
        },
        "columns": {
            "summary": {"header_tree": header_tree}
        },
        "drilldowns": {
            "metric_drill": {
                "title": "{cell.column} 明细 — {row.domain_code} · {filter.business_date.from} ~ {filter.business_date.to}",
                "endpoint": f"/api/reports/{report_type}/drilldown",
                "method": "POST",
                "param_mapping": {
                    "row.domain_code": "domain_code",
                    "row.project_code": "project_code",
                    "cell.column": "metric_code",
                    "filter.business_date.from": "date_from",
                    "filter.business_date.to": "date_to",
                    "filter.projects": "project_codes"
                },
                "paging": {"enabled": True, "default_page_size": 25, "default_mode": "server"},
                "sortable": True,
                "default_sort": "period_date:desc,ref_id:desc",
                "row_filter": {"enabled": True},
                "header_tree": [
                    {"code": "_id", "label": "标识", "children": [
                        {"code": "ref_id",      "label": "引用号", "data_type": "string", "is_default_visible": True,
                         "default_order": 1, "default_pinned": "left", "default_width": 220,
                         "sortable": True, "row_filterable": True, "display": {"kind": "text"}},
                        {"code": "ref_label",   "label": "标题",   "data_type": "string", "is_default_visible": True,
                         "default_order": 2, "default_width": 280,
                         "sortable": False, "row_filterable": True, "display": {"kind": "text"}},
                    ]},
                    {"code": "_meta", "label": "归属", "children": [
                        {"code": "project_code","label": "项目",   "data_type": "string", "is_default_visible": True,
                         "default_order": 3, "default_width": 110,
                         "sortable": True, "row_filterable": True, "display": {"kind": "text"}},
                        {"code": "domain_code", "label": "领域",   "data_type": "string", "is_default_visible": True,
                         "default_order": 4, "default_width": 100,
                         "sortable": True, "row_filterable": True, "display": {"kind": "text"}},
                        {"code": "period_date", "label": "日期",   "data_type": "date",   "is_default_visible": True,
                         "default_order": 5, "default_width": 110,
                         "sortable": True, "row_filterable": True, "display": {"kind": "text"}},
                    ]},
                    {"code": "_flags", "label": "标记", "children": [
                        {"code": "detail_type", "label": "类型",   "data_type": "string", "is_default_visible": True,
                         "default_order": 6, "default_width": 100,
                         "sortable": True, "row_filterable": True, "display": {"kind": "text"}},
                        {"code": "source",      "label": "来源",   "data_type": "string", "is_default_visible": True,
                         "default_order": 7, "default_width": 90,
                         "sortable": True, "row_filterable": True, "display": {"kind": "text"}},
                        {"code": "version_no",  "label": "版本",   "data_type": "int",    "is_default_visible": True,
                         "default_order": 8, "default_width": 70,
                         "sortable": True, "row_filterable": True, "display": {"kind": "number"}},
                        {"code": "is_ai_generated", "label": "AI 生成", "data_type": "bool", "is_default_visible": True,
                         "default_order": 9, "default_width": 80,
                         "sortable": True, "row_filterable": True, "display": {"kind": "text"}},
                        {"code": "is_adopted",  "label": "已采纳", "data_type": "bool",   "is_default_visible": True,
                         "default_order": 10, "default_width": 80,
                         "sortable": True, "row_filterable": True, "display": {"kind": "text"}},
                        {"code": "severity",    "label": "严重度", "data_type": "string", "is_default_visible": False,
                         "default_order": 11, "default_width": 80,
                         "sortable": True, "row_filterable": True, "display": {"kind": "text"}},
                        {"code": "author",      "label": "负责人", "data_type": "string", "is_default_visible": False,
                         "default_order": 12, "default_width": 90,
                         "sortable": True, "row_filterable": True, "display": {"kind": "text"}},
                    ]},
                ]
            }
        }
    }
