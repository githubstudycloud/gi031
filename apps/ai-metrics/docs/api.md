# API 文档 · AI 测试度量看板

> Base URL: `http://<host>:8001`（本地 / 192.168.0.132 部署后用对应 IP）
> 协议：所有响应统一信封 `{code, message, trace_id, data}`，错误时 code != 0。
> OpenAPI / Swagger UI：`GET /docs`（FastAPI 自动生成）

## 端点速查

| 路径 | 方法 | 作用 |
|---|---|---|
| `/api/healthz` | GET | 健康检查 |
| `/api/reports/ai_metrics/config` | GET | 拿报表完整配置（filters + header_tree + drilldowns）|
| `/api/dropdowns/projects` | GET | 项目下拉数据 |
| `/api/reports/ai_metrics/summary` | POST | 主表数据（含 totals_row）|
| `/api/reports/ai_metrics/distinct` | POST | 列值去重（filter 弹框用）|
| `/api/reports/ai_metrics/drilldown` | POST | 单元格下钻明细 |
| `/api/metrics/ingest` | POST | 批量上报指标值 |
| `/api/metrics/ingest_csv` | POST | CSV 批量导入 |
| `/api/metrics/ingest_details` | POST | 上报下钻明细行 |

---

## 1. `GET /api/reports/ai_metrics/config`

```bash
curl -s http://localhost:8001/api/reports/ai_metrics/config | python -m json.tool
```

返回（节选）：
```jsonc
{
  "code": 0,
  "data": {
    "meta": { "report_type":"ai_metrics", "name":"AI 测试度量看板", "version":1 },
    "primary_keys": ["domain_code"],
    "filters": [
      { "code":"business_date", "label":"时间段", "kind":"date_range", "required":true,
        "param":{"from":"date_from","to":"date_to"} },
      { "code":"projects", "label":"项目 (多选)", "kind":"multi_select", "required":true,
        "param":{"value":"project_codes"},
        "source":{"endpoint":"/api/dropdowns/projects", ...} }
    ],
    "primary_view": {
      "endpoint":"/api/reports/ai_metrics/summary", "method":"POST",
      "paging":{"enabled":true, "default_page_size":200, "default_mode":"client"},
      "row_totals": true
    },
    "columns": {
      "summary": { "header_tree": [
        {"code":"_dim","label":"领域","children":[ {...domain_code 列...} ]},
        {"code":"_测试设计","label":"测试设计","children":[ {...7 个指标列...} ]},
        {"code":"_测试脚本生成","label":"测试脚本生成","children":[ {...4 个指标列...} ]}
      ]}
    },
    "drilldowns": {
      "metric_drill": { "title":"...", "endpoint":"/api/reports/ai_metrics/drilldown",
        "method":"POST", "param_mapping":{...}, "header_tree":[...] }
    }
  }
}
```

`meta.version` 在 `metric_def` 表变更时手工 +1；前端用它做缓存失效。

---

## 2. `GET /api/dropdowns/projects`

| Query | 说明 |
|---|---|
| `q` | 模糊搜索 (label 或 code) |
| `page` / `page_size` | 分页（page_size 默认 50） |
| `sort` | 排序，例 `label:asc` |
| `only_favorites` | 1 = 仅返回当前用户收藏（V1 占位）|

```bash
curl -s "http://localhost:8001/api/dropdowns/projects?q=alpha&page=1&page_size=10"
```

```jsonc
{ "code":0, "data": {
  "items": [ {"value":"proj_alpha","label":"Alpha 内容平台","is_favorite":false} ],
  "page":1, "page_size":10, "total":1, "has_more":false
}}
```

---

## 3. `POST /api/reports/ai_metrics/summary`

**核心接口**：返回按领域分组的全量度量行 + 一行 `extras.totals_row`。

请求体：

```jsonc
{
  "date_from": "2026-05-01",            // 可空 = 不限
  "date_to":   "2026-05-15",
  "project_codes": ["proj_alpha", "proj_beta"],   // 可空 = 所有项目
  "row_dim": "domain",                  // V1 only; V1.1 "project>domain" / "org>domain"
  "paging": "none",                     // "server" or "none"
  "page": 1, "page_size": 200,
  "sort": "ai_code_lines:desc",         // 可空
  "row_filter": []                      // 可空，详见 03-api §2.1.1
}
```

curl：

```bash
curl -s -X POST http://localhost:8001/api/reports/ai_metrics/summary \
  -H "Content-Type: application/json" \
  -d '{
    "date_from":"2026-05-01",
    "date_to":"2026-05-15",
    "project_codes":["proj_alpha","proj_beta"],
    "paging":"none"
  }' | python -m json.tool
```

httpie：

```bash
http POST :8001/api/reports/ai_metrics/summary \
  date_from=2026-05-01 date_to=2026-05-15 \
  project_codes:='["proj_alpha"]' paging=none
```

Python：

```python
import httpx
r = httpx.post(
    "http://localhost:8001/api/reports/ai_metrics/summary",
    json={
        "date_from": "2026-05-01", "date_to": "2026-05-15",
        "project_codes": ["proj_alpha", "proj_beta"], "paging": "none",
    },
    timeout=15,
)
data = r.json()["data"]
for row in data["items"]:
    print(row["domain_code"], row["ai_req_coverage"], "%")
print("TOTAL:", data["extras"]["totals_row"])
```

响应（节选）：

```jsonc
{ "code":0, "data": {
  "items": [
    {
      "domain_code": "核心域",
      "_domain_code_raw": "core",
      "req_count": 279.0, "ai_req_count": 128.0,
      "new_case_count": 809.0, "ai_case_count": 466.0, "ai_case_adopted": 221.0,
      "ai_code_lines": 13200.0, "total_code_lines": 22960.0,
      "ai_code_accurate_lines": 12251.0,
      "new_script_count": 121.0, "new_script_ai_assisted_count": 53.0,
      "ai_req_coverage": 45.88,
      "ai_case_ratio": 57.6,
      "ai_case_adoption_rate": 47.42,
      "ai_script_code_ratio": 57.49,
      "ai_code_accuracy": 92.81,
      "new_script_ai_ratio": 43.8
    },
    /* …其他 5 行领域… */
  ],
  "total": 6, "page": 1, "page_size": 6, "has_more": false,
  "extras": { "totals_row": {
    "req_count": 1786.0, ..., "ai_req_coverage": 49.21, "domain_code": "合计", "_is_total": true
  }}
}}
```

---

## 4. `POST /api/reports/ai_metrics/distinct`

列值去重：

```bash
curl -s -X POST http://localhost:8001/api/reports/ai_metrics/distinct \
  -H "Content-Type: application/json" \
  -d '{"column":"domain_code","date_from":"2026-05-01","date_to":"2026-05-15"}'
```

```jsonc
{"code":0,"data":{"column":"domain_code","items":[
  {"value":"core","label":"核心域","count":52},
  {"value":"business","label":"业务域","count":48},
  ...
],"truncated":false}}
```

---

## 5. `POST /api/reports/ai_metrics/drilldown`

请求体：

```jsonc
{
  "ref": "metric_drill",
  "row":  { "domain_code": "core" },
  "filter": {
    "business_date": {"from":"2026-05-01","to":"2026-05-15"},
    "projects": ["proj_alpha","proj_beta"]
  },
  "cell": { "column": "ai_case_count" },
  "paging": {"page": 1, "page_size": 30}
}
```

```bash
curl -s -X POST http://localhost:8001/api/reports/ai_metrics/drilldown \
  -H "Content-Type: application/json" \
  -d '{"ref":"metric_drill","row":{"domain_code":"core"},"filter":{"business_date":{"from":"2026-05-13","to":"2026-05-15"},"projects":["proj_alpha"]},"cell":{"column":"ai_case_count"},"paging":{"page":1,"page_size":10}}'
```

返回 `ai_metric_detail` 行。

---

## 6. 数据上报

### 6.1 JSON 批量

```bash
curl -X POST http://localhost:8001/api/metrics/ingest \
  -H "Content-Type: application/json" \
  -d '{"items":[
    {"period_date":"2026-05-15","project_code":"proj_alpha","domain_code":"core",
     "metric_code":"req_count","metric_value":12,"source":"jira-cron"},
    {"period_date":"2026-05-15","project_code":"proj_alpha","domain_code":"core",
     "metric_code":"ai_req_count","metric_value":7,"source":"jira-cron"}
  ]}'
```

幂等：唯一键 `(period_date, project_code, domain_code, iteration_code, metric_code, source)`。重复上报会更新 `metric_value`。

### 6.2 CSV 导入

```csv
period_date,project_code,domain_code,iteration_code,metric_code,metric_value,source
2026-05-15,proj_alpha,core,,req_count,12,jira-cron
2026-05-15,proj_alpha,core,,ai_req_count,7,jira-cron
```

```bash
curl -X POST -F "file=@metrics.csv" http://localhost:8001/api/metrics/ingest_csv
```

### 6.3 下钻明细上报

```bash
curl -X POST http://localhost:8001/api/metrics/ingest_details \
  -H "Content-Type: application/json" \
  -d '{"items":[{
    "period_date":"2026-05-15", "project_code":"proj_alpha", "domain_code":"core",
    "metric_code":"ai_case_count", "detail_type":"case",
    "ref_id":"CASE-12345", "ref_label":"AI 生成登录用例",
    "ref_url":"https://testrail.example.com/case/12345",
    "is_ai_generated": true, "is_adopted": true,
    "payload": {"author":"ai-bot","reviewer":"qa-zhang"}
  }]}'
```

---

## 7. 扩展点（保留接口）

| 想要的能力 | 怎么扩 |
|---|---|
| 加新指标（如"AI 评审建议采纳数"）| 插一行 `metric_def` + 上报到 `ai_metric`；前端 `/config` 自动加列 |
| 加新一级表头分组 | 设置 `metric_def.category` 为新值即可 |
| 维度变 "项目 + 迭代" | `POST /summary` 加 `row_dim: "project>domain"` 或 `iteration>domain`（V1.1 实现）|
| 维度变 4 层组织 | 用 `dim_org` + `row_dim: "org>domain"`（V1.2）|
| 加视图模板（看板/紧凑） | 加 `view_template` 行；前端 🎨 切换即用 |

## 8. 错误响应示例

```jsonc
{ "code": 4001, "message": "row_dim=org>domain not supported yet", "trace_id":"abc...", "data": null }
```

错误码段同主设计（见 [docs/design/03-api-spec §6](../../../docs/design/03-api-spec.md)）。

## 9. 鉴权（V1 不开 / 占位）

V1 接口无鉴权（同公司内网 + Nginx allow 列表）。
V1.1 接入 JWT (RS256)：`Authorization: Bearer <token>`。
