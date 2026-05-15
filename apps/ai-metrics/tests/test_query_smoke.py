"""查询模块独立 smoke test：构表 + seed + 跑全部查询路由 + 校验结构。

跑：
    pytest -q
仅这些测：
    pytest tests/test_query_smoke.py -q -k smoke
"""
from __future__ import annotations


def test_healthz(client):
    r = client.get("/api/healthz")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_config_smoke(client):
    r = client.get("/api/reports/ai_metrics/config")
    assert r.status_code == 200
    data = r.json()["data"]

    # 核心字段
    assert data["meta"]["report_type"] == "ai_metrics"
    assert data["primary_keys"] == ["domain_code"]
    assert data["primary_view"]["row_totals"] is True

    # filters
    fcodes = [f["code"] for f in data["filters"]]
    assert "business_date" in fcodes and "projects" in fcodes

    # header_tree: 至少 3 个一级分组（领域 + 测试设计 + 测试脚本生成）
    tree = data["columns"]["summary"]["header_tree"]
    assert len(tree) >= 3
    # 找叶子总数（应 >= 12）
    def leaves(n): return [n] if "children" not in n else [l for c in n["children"] for l in leaves(c)]
    flat = [l for g in tree for l in leaves(g)]
    leaf_codes = {l["code"] for l in flat}
    for must in ["domain_code", "req_count", "ai_req_count", "ai_case_count",
                 "ai_code_lines", "ai_req_coverage", "ai_case_ratio",
                 "ai_script_code_ratio", "ai_code_accuracy"]:
        assert must in leaf_codes, f"missing leaf column: {must}"


def test_dropdown_projects(client):
    r = client.get("/api/dropdowns/projects")
    assert r.status_code == 200
    items = r.json()["data"]["items"]
    assert len(items) >= 4
    assert all("value" in i and "label" in i for i in items)


def test_summary_returns_rows_and_totals(client):
    r = client.post("/api/reports/ai_metrics/summary",
                    json={"paging": "none", "project_codes": None})
    assert r.status_code == 200
    data = r.json()["data"]
    assert "items" in data and len(data["items"]) >= 6   # 6 个领域
    # 关键 computed 列存在
    sample = data["items"][0]
    for col in ["domain_code", "req_count", "ai_req_count", "ai_req_coverage",
                "new_case_count", "ai_case_count", "ai_case_ratio",
                "ai_case_adoption_rate", "ai_script_code_ratio",
                "ai_code_lines", "ai_code_accuracy", "new_script_ai_ratio"]:
        assert col in sample, f"missing column in summary row: {col}"
    # totals_row 在 extras
    assert "extras" in data and "totals_row" in data["extras"]
    tr = data["extras"]["totals_row"]
    assert tr["domain_code"] == "合计"
    assert tr["_is_total"] is True
    # 合计的 req_count 必然 >= 任意一行的 req_count（除非该行为 0）
    max_per_row = max(row["req_count"] for row in data["items"])
    assert tr["req_count"] >= max_per_row


def test_summary_with_project_filter(client):
    # 先取项目列表
    rs = client.get("/api/dropdowns/projects").json()["data"]["items"]
    one = rs[0]["value"]
    r = client.post("/api/reports/ai_metrics/summary",
                    json={"paging": "none", "project_codes": [one]})
    assert r.status_code == 200
    items = r.json()["data"]["items"]
    # 仍然按领域分组（6 行），但每行数据规模约 1/4
    assert len(items) >= 1


def test_distinct(client):
    r = client.post("/api/reports/ai_metrics/distinct",
                    json={"column": "domain_code"})
    assert r.status_code == 200
    items = r.json()["data"]["items"]
    assert len(items) >= 6
    # label 应该是领域名而不是 code
    labels = {i["label"] for i in items}
    assert "核心域" in labels or any("域" in l for l in labels)


def test_drilldown(client):
    r = client.post("/api/reports/ai_metrics/drilldown",
                    json={
                        "ref": "metric_drill",
                        "row": {"domain_code": "core"},
                        "filter": {"business_date": {"from": None, "to": None}, "projects": []},
                        "cell": {"column": "ai_case_count"},
                        "paging": {"page": 1, "page_size": 20}
                    })
    assert r.status_code == 200
    data = r.json()["data"]
    assert "items" in data
    # 明细行可能为空（seed 的 detail 只覆盖前 2 天）；至少接口语义正确
    assert "total" in data and "has_more" in data


def test_view_templates(client):
    r = client.get("/api/view_templates/ai_metrics")
    assert r.status_code == 200
    items = r.json()["data"]["items"]
    assert len(items) >= 2  # seed 了 weekly_finance + script_focus
    codes = {it["code"] for it in items}
    assert "weekly_finance" in codes
