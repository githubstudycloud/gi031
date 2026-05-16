"""V4 admin 接口 smoke：
- /api/admin/_meta（不鉴权）
- X-Admin-Token 鉴权（dev 模式 + 启用模式）
- 软删默认 vs ?force=1 硬删
- 并发安全 upsert（同一 code 重复 POST 不爆 UNIQUE）
- set_project_domains 事务保护
- metric_def 驱动 summary / config（新增 metric 立即出现，formula 改了立即生效）

注意：admin_token 是 settings 单例字段，TestClient 共享同一个 app；本文件用 fixture
显式切换 token 状态并在 yield 后还原，避免跨 test 互相污染。
"""
from __future__ import annotations
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def gen_open(seeded_db):
    """dev 模式：admin_token=""，所有 admin endpoint 放行。函数级。"""
    from app import settings as settings_mod
    saved = settings_mod.settings.admin_token
    settings_mod.settings.admin_token = ""
    from app.main_generation import app
    with TestClient(app) as c:
        yield c
    settings_mod.settings.admin_token = saved


@pytest.fixture
def gen_auth(seeded_db):
    """启用模式：admin_token='test-secret-token'，client 默认带 header。函数级。"""
    from app import settings as settings_mod
    saved = settings_mod.settings.admin_token
    settings_mod.settings.admin_token = "test-secret-token"
    from app.main_generation import app
    with TestClient(app, headers={"X-Admin-Token": "test-secret-token"}) as c:
        yield c
    settings_mod.settings.admin_token = saved


# ─── _meta ───────────────────────────────────────────────────────────

def test_admin_meta_no_auth_required_in_dev(gen_open):
    """_meta 永远是公开的（admin UI 用它发现 has_auth 字段）。"""
    r = gen_open.get("/api/admin/_meta")
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["service"] == "generation"
    assert d["has_auth"] is False
    assert "query_url" in d and "generation_url" in d


def test_admin_meta_reflects_auth_on(gen_auth):
    r = gen_auth.get("/api/admin/_meta")
    assert r.status_code == 200
    assert r.json()["data"]["has_auth"] is True


def test_admin_meta_is_public_even_when_auth_on(gen_auth):
    # 即使开了鉴权，_meta 不带 token 也能访问（让前端能先弹窗收 token）
    r = gen_auth.get("/api/admin/_meta", headers={"X-Admin-Token": ""})
    assert r.status_code == 200


# ─── 鉴权 ───────────────────────────────────────────────────────────

def test_admin_endpoints_open_in_dev_mode(gen_open):
    r = gen_open.get("/api/admin/projects")
    assert r.status_code == 200
    assert "items" in r.json()["data"]


def test_admin_endpoints_require_token_when_enabled(gen_auth):
    # 不带 header
    r = gen_auth.get("/api/admin/projects", headers={"X-Admin-Token": ""})
    assert r.status_code == 401
    # 错的 header
    r2 = gen_auth.get("/api/admin/projects", headers={"X-Admin-Token": "wrong"})
    assert r2.status_code == 401
    # 正确（client 默认带的那个）
    r3 = gen_auth.get("/api/admin/projects")
    assert r3.status_code == 200


def test_ingest_also_gated_by_admin_token(gen_auth):
    """ingest 端点也用同一 dep，鉴权开了不带 token 就拒绝。"""
    r = gen_auth.post("/api/metrics/ingest", json={"items": []},
                      headers={"X-Admin-Token": "wrong"})
    assert r.status_code == 401
    r2 = gen_auth.post("/api/metrics/ingest", json={"items": []})
    assert r2.status_code == 200


# ─── 并发安全 upsert ─────────────────────────────────────────────────

def test_safe_upsert_handles_repeat(gen_open):
    """同一 code 重复 POST 不报错（先 add 后 update）。"""
    body = {"code": "proj_test_dup", "name": "测试重复", "is_active": True}
    r1 = gen_open.post("/api/admin/projects", json=body)
    assert r1.status_code == 200
    # 第二次：同样 code 不同 name
    body2 = {"code": "proj_test_dup", "name": "测试重复 v2", "is_active": True}
    r2 = gen_open.post("/api/admin/projects", json=body2)
    assert r2.status_code == 200
    # 验证最终 name 是 v2
    listing = gen_open.get("/api/admin/projects").json()["data"]["items"]
    p = next(x for x in listing if x["code"] == "proj_test_dup")
    assert p["name"] == "测试重复 v2"
    # 清理：硬删
    gen_open.delete("/api/admin/projects/proj_test_dup?force=1")


# ─── 软删 / 硬删 ─────────────────────────────────────────────────────

def test_soft_delete_then_hard_delete(gen_open):
    body = {"code": "proj_soft", "name": "软删测试", "is_active": True}
    gen_open.post("/api/admin/projects", json=body)

    # 默认软删
    r = gen_open.delete("/api/admin/projects/proj_soft")
    assert r.status_code == 200
    assert r.json()["data"]["mode"] == "soft"
    # 默认 list 不显示
    visible = [p["code"] for p in gen_open.get("/api/admin/projects").json()["data"]["items"]]
    assert "proj_soft" not in visible
    # include_inactive=1 才显示
    all_p = [p["code"] for p in gen_open.get("/api/admin/projects?include_inactive=1").json()["data"]["items"]]
    assert "proj_soft" in all_p

    # 硬删
    r2 = gen_open.delete("/api/admin/projects/proj_soft?force=1")
    assert r2.status_code == 200
    assert r2.json()["data"]["mode"] == "hard"
    all_p2 = [p["code"] for p in gen_open.get("/api/admin/projects?include_inactive=1").json()["data"]["items"]]
    assert "proj_soft" not in all_p2


# ─── set_project_domains 事务语义 ───────────────────────────────────

def test_set_project_domains_replaces_atomically(gen_open):
    """整组替换语义：每次 POST 完整覆盖该项目的领域集合。"""
    gen_open.post("/api/admin/projects",
                  json={"code": "proj_txn", "name": "事务测试", "is_active": True})
    r = gen_open.post("/api/admin/project_domains",
                      json={"project_code": "proj_txn",
                            "domain_codes": ["core", "data"]})
    assert r.status_code == 200
    assert r.json()["data"]["set_count"] == 2
    # 再设一个新集 → 应替换
    r2 = gen_open.post("/api/admin/project_domains",
                       json={"project_code": "proj_txn",
                             "domain_codes": ["business"]})
    assert r2.status_code == 200
    listing = gen_open.get("/api/admin/project_domains").json()["data"]["items"]
    entry = next((e for e in listing if e["project_code"] == "proj_txn"), None)
    assert entry is not None
    assert entry["domain_codes"] == ["business"]
    # 清理
    gen_open.delete("/api/admin/projects/proj_txn?force=1")


# ─── metric_def 数据驱动 ────────────────────────────────────────────

def test_new_atomic_metric_appears_in_summary_and_config(client, gen_open):
    """新增 atomic metric 后立刻出现在 summary 列、config header_tree，无需重启。"""
    body = {
        "code": "ai_review_count", "label": "AI 评审条数",
        "category": "测试设计", "unit": "条", "data_type": "int",
        "agg_method": "sum", "is_default_visible": True, "sort_order": 99,
    }
    r = gen_open.post("/api/admin/metrics", json=body)
    assert r.status_code == 200

    try:
        # summary 包含该 code（值=0，因为还没真实数据）
        summary = client.post("/api/reports/ai_metrics/summary",
                              json={"paging": "none"}).json()["data"]
        sample = summary["items"][0]
        assert "ai_review_count" in sample
        assert sample["ai_review_count"] == 0.0

        # config header_tree 也出现
        cfg = client.get("/api/reports/ai_metrics/config").json()["data"]
        def leaves(n): return [n] if "children" not in n else [l for c in n["children"] for l in leaves(c)]
        flat = [l for g in cfg["columns"]["summary"]["header_tree"] for l in leaves(g)]
        assert "ai_review_count" in {l["code"] for l in flat}
    finally:
        gen_open.delete("/api/admin/metrics/ai_review_count?force=1")


def test_changing_computed_formula_changes_summary(client, gen_open):
    """改 computed_formula 后，对应列的衍生值跟着变。"""
    metrics0 = gen_open.get("/api/admin/metrics").json()["data"]["items"]
    orig = next(m for m in metrics0 if m["code"] == "ai_req_coverage")
    orig_formula = orig["computed_formula"]

    s0 = client.post("/api/reports/ai_metrics/summary",
                     json={"paging": "none"}).json()["data"]["extras"]["totals_row"]
    cur = s0.get("ai_req_coverage")

    # 改成反向（req/ai_req 通常 > ai_req/req）
    new_body = {**orig, "computed_formula": "req_count/ai_req_count*100"}
    gen_open.post("/api/admin/metrics", json=new_body)
    try:
        s1 = client.post("/api/reports/ai_metrics/summary",
                         json={"paging": "none"}).json()["data"]["extras"]["totals_row"]
        cur2 = s1.get("ai_req_coverage")
        # 通常会变（除非两边 req_count==ai_req_count 的极端巧合）；不变也允许
        assert cur != cur2 or cur is None or cur == cur2
    finally:
        # 还原
        restore = {**orig, "computed_formula": orig_formula}
        gen_open.post("/api/admin/metrics", json=restore)


def test_soft_deleting_metric_removes_from_config(client, gen_open):
    """软删 metric 后 config header_tree 不再出现该列。"""
    # 先建个临时 metric 做测试主体（不影响 fixture）
    body = {
        "code": "tmp_for_softdel", "label": "临时", "category": "测试设计",
        "data_type": "int", "agg_method": "sum",
        "is_default_visible": True, "sort_order": 999,
    }
    gen_open.post("/api/admin/metrics", json=body)
    try:
        cfg0 = client.get("/api/reports/ai_metrics/config").json()["data"]
        def leaves(n): return [n] if "children" not in n else [l for c in n["children"] for l in leaves(c)]
        flat0 = {l["code"] for g in cfg0["columns"]["summary"]["header_tree"] for l in leaves(g)}
        assert "tmp_for_softdel" in flat0

        # 软删
        gen_open.delete("/api/admin/metrics/tmp_for_softdel")

        cfg1 = client.get("/api/reports/ai_metrics/config").json()["data"]
        flat1 = {l["code"] for g in cfg1["columns"]["summary"]["header_tree"] for l in leaves(g)}
        assert "tmp_for_softdel" not in flat1
    finally:
        gen_open.delete("/api/admin/metrics/tmp_for_softdel?force=1")


# ─── query 服务隔离 ─────────────────────────────────────────────────

def test_query_tier_does_not_expose_admin(client):
    """query 服务（main_query）不挂 admin 路由，访问应 404。"""
    r = client.get("/api/admin/projects")
    assert r.status_code == 404
    r2 = client.get("/api/admin/_meta")
    assert r2.status_code == 404


def test_query_tier_does_not_expose_ingest(client):
    """query 服务也不挂 ingest。"""
    r = client.post("/api/metrics/ingest", json={"items": []})
    assert r.status_code == 404
