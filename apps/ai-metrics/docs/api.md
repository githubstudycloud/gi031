# API 文档 (ai-metrics)

> 全部走 `Content-Type: application/json`；返回统一信封 `{code, message, trace_id, data}`。
> 本地开发：`http://127.0.0.1:8001`；远端：`http://192.168.0.132:8001`。
> Swagger UI：`/docs` · ReDoc：`/redoc`。

## 0. 信封约定

成功响应：

```jsonc
{
  "code": 0,
  "message": "ok",
  "trace_id": null,
  "data": { ... }    // 具体见每个接口
}
```

错误响应（FastAPI 默认）：

```jsonc
{ "detail": "..." }   // 422 (validation) / 400 / 404 / 500
```

业务错误码（V1 占位）：
- `0`   成功
- 后续按需扩 `4xxx` (业务) / `5xxx` (系统)

## 1. 健康检查

```bash
curl http://127.0.0.1:8001/api/healthz
# → {"status":"ok"}
```

## 2. 报表配置（前端自描述协议）

`GET /api/reports/{report_type}/config`

```bash
curl -s http://127.0.0.1:8001/api/reports/ai_metrics/config | jq '.data | {meta, primary_keys, filters}'
```

返回（节选）：

```jsonc
{
  "meta": { "report_type": "ai_metrics", "name": "AI 测试度量看板", "version": 1 },
  "primary_keys": ["domain_code"],
  "filters": [
    { "code": "business_date", "kind": "date_range", "required": true,
      "default": {"preset": "last_30_days"}, "param": {"from": "date_from", "to": "date_to"} },
    { "code": "projects", "kind": "multi_select",
      "source": { "endpoint": "/api/dropdowns/projects", "supports_favorite": true, "max_picks": 50 } }
  ],
  "columns": {
    "summary": {
      "header_tree": [
        { "code": "_dim", "label": "领域", "children": [...] },
        { "code": "_测试设计", "label": "测试设计", "children": [...] },
        { "code": "_测试脚本生成", "label": "测试脚本生成", "children": [...] }
      ]
    }
  },
  "primary_view": {
    "endpoint": "/api/reports/ai_metrics/summary",
    "paging": { "default_page_size": 200, "default_mode": "client" },
    "row_totals": true
  }
}
```

## 3. 项目下拉

`GET /api/dropdowns/projects?q=&page=1&page_size=50`

```bash
curl -s "http://127.0.0.1:8001/api/dropdowns/projects?q=alpha"
```

```jsonc
{
  "code": 0,
  "data": {
    "items": [
      { "value": "proj_alpha", "label": "Alpha 内容平台", "is_favorite": false }
    ],
    "page": 1, "page_size": 50, "total": 1, "has_more": false
  }
}
```

## 4. 汇总查询（核心接口）

`POST /api/reports/{report_type}/summary`

```bash
curl -s -X POST http://127.0.0.1:8001/api/reports/ai_metrics/summary \
  -H "Content-Type: application/json" \
  -d '{
    "date_from": "2026-04-15",
    "date_to":   "2026-05-15",
    "project_codes": ["proj_alpha", "proj_beta"],
    "paging": "none",
    "row_dim": "domain"
  }' | jq '.data.items[0]'
```

返回（节选）：

```jsonc
{
  "code": 0,
  "data": {
    "items": [
      {
        "domain_code": "核心域",
        "_domain_code_raw": "core",
        "_row_key": { "domain_code": "core" },
        "req_count": 404,
        "ai_req_count": 229,
        "ai_req_coverage": 56.68,
        "new_case_count": 1092,
        "ai_case_count": 531,
        "ai_case_ratio": 48.63,
        "ai_case_adoption_rate": 44.82,
        "ai_script_code_ratio": 60.94,
        "ai_code_lines": 19340,
        "ai_code_accuracy": 88.21,
        "new_script_ai_ratio": 51.12
      }
    ],
    "page": 1, "page_size": 6, "total": 6, "has_more": false,
    "extras": {
      "totals_row": {
        "domain_code": "合计",
        "_is_total": true,
        "req_count": 2263,
        "ai_req_coverage": 51.83
      }
    }
  }
}
```

**字段语义**：

| 列 | 类型 | 聚合规则 |
|---|---|---|
| `req_count` / `ai_req_count` / `new_case_count` / `ai_case_count` / `ai_code_lines` / `total_code_lines` / `new_script_count` | int | sum |
| `ai_req_coverage` = `ai_req_count / req_count * 100` | percent | computed |
| `ai_case_ratio` = `ai_case_count / new_case_count * 100` | percent | computed |
| `ai_case_adoption_rate` = `ai_case_adopted / ai_case_count * 100` | percent | weighted_avg (权重=ai_case_count) |
| `ai_script_code_ratio` = `ai_code_lines / total_code_lines * 100` | percent | computed |
| `ai_code_accuracy` = `ai_code_accurate_lines / ai_code_lines * 100` | percent | weighted_avg (权重=ai_code_lines) |
| `new_script_ai_ratio` = `new_script_ai_assisted_count / new_script_count * 100` | percent | weighted_avg |

## 5. 列值去重（列筛选用）

`POST /api/reports/{report_type}/distinct`

```bash
curl -s -X POST http://127.0.0.1:8001/api/reports/ai_metrics/distinct \
  -H "Content-Type: application/json" \
  -d '{"column": "domain_code"}'
```

```jsonc
{
  "code": 0,
  "data": {
    "column": "domain_code",
    "items": [
      { "value": "core",     "label": "核心域", "count": 1280 },
      { "value": "business", "label": "业务域", "count": 1242 }
    ],
    "truncated": false
  }
}
```

## 6. 下钻明细

`POST /api/reports/{report_type}/drilldown`

```bash
curl -s -X POST http://127.0.0.1:8001/api/reports/ai_metrics/drilldown \
  -H "Content-Type: application/json" \
  -d '{
    "ref": "metric_drill",
    "row":  { "domain_code": "core" },
    "filter": {
      "business_date": { "from": "2026-05-13", "to": "2026-05-15" },
      "projects": ["proj_alpha"]
    },
    "cell": { "column": "ai_case_count" },
    "paging": { "page": 1, "page_size": 20 }
  }'
```

```jsonc
{
  "code": 0,
  "data": {
    "items": [
      {
        "ref_id": "JIRA-CASE-proj_alpha-core-2026-05-15-0",
        "ref_label": "[Jira] AI 用例 core/1",
        "ref_url": "https://jira.example.com/case/123",
        "detail_type": "case",
        "is_ai_generated": true,
        "is_adopted": null,
        "period_date": "2026-05-15",
        "payload": {"source": "jira", "scrape_at": "2026-05-15"}
      }
    ],
    "page": 1, "page_size": 20, "total": 3, "has_more": false
  }
}
```

## 7. 视图模板（前端切换）

`GET /api/view_templates/{report_type}`

```bash
curl -s http://127.0.0.1:8001/api/view_templates/ai_metrics | jq '.data.items[].code'
```

```jsonc
{
  "code": 0,
  "data": {
    "items": [
      {
        "code": "weekly_finance", "name": "周度财务汇报", "source": "backend", "scope": "global",
        "config": {
          "density": "normal", "page_size": 200, "paging_mode": "client",
          "show_kpi": true,
          "kpi": [
            {"label": "AI 用例数", "source": "totals.ai_case_count", "format": {"kind":"number","thousand":true}}
          ],
          "only_columns": ["domain_code","req_count","ai_req_count","ai_req_coverage","ai_case_count","ai_case_adoption_rate","ai_code_lines"],
          "default_sort": {"field": "ai_code_lines", "dir": "desc"}
        }
      }
    ]
  }
}
```

## 8. 数据上报 (Ingest)

> 离线模拟模式下用 `python -m app.sources.simulate`；真实接入用以下两个端点。

### 8.1 单条 / 批量上报

`POST /api/metrics/ingest`

```bash
curl -X POST http://127.0.0.1:8001/api/metrics/ingest \
  -H "Content-Type: application/json" \
  -d '{
    "items": [
      {
        "period_date": "2026-05-15",
        "project_code": "proj_alpha",
        "domain_code":  "core",
        "metric_code":  "req_count",
        "metric_value": 12,
        "source": "manual"
      }
    ]
  }'
# → {"code":0, "data":{"ingested": 1}}
```

幂等键：`(period_date, project_code, domain_code, iteration_code, metric_code, source)`。
重复 POST 同样的键 → 更新 `metric_value`，不会重复入。

### 8.2 CSV 批量

`POST /api/metrics/ingest_csv`

CSV 格式：

```csv
period_date,project_code,domain_code,iteration_code,metric_code,metric_value,source
2026-05-15,proj_alpha,core,,req_count,12,manual
2026-05-15,proj_alpha,core,,ai_req_count,8,manual
```

```bash
curl -X POST http://127.0.0.1:8001/api/metrics/ingest_csv \
  -F "file=@data.csv"
```

### 8.3 明细上报（用于下钻）

`POST /api/metrics/ingest_details`

```bash
curl -X POST http://127.0.0.1:8001/api/metrics/ingest_details \
  -H "Content-Type: application/json" \
  -d '{
    "items": [
      {
        "period_date": "2026-05-15",
        "project_code": "proj_alpha",
        "domain_code":  "core",
        "metric_code":  "ai_case_count",
        "detail_type":  "case",
        "ref_id":       "TC-2026051501",
        "ref_label":    "登录-异常重试用例",
        "ref_url":      "https://testrail.example.com/case/123",
        "is_ai_generated": true,
        "is_adopted":   true,
        "payload": {"author":"alice"}
      }
    ]
  }'
```

## 9. 一键 smoke verification

```bash
bash deploy/deploy-offline.sh verify
# 输出 7 个端点 + 前端静态资源的 200/失败
```

## 10. Python 客户端示例

```python
import httpx
from datetime import date

client = httpx.Client(base_url="http://192.168.0.132:8001")

# 1. 写一批指标
r = client.post("/api/metrics/ingest", json={
    "items": [
        {"period_date": date.today().isoformat(),
         "project_code": "proj_alpha", "domain_code": "core",
         "metric_code": "req_count", "metric_value": 12, "source": "jira"},
        {"period_date": date.today().isoformat(),
         "project_code": "proj_alpha", "domain_code": "core",
         "metric_code": "ai_req_count", "metric_value": 8, "source": "jira"},
    ]
})
print("ingest:", r.json())

# 2. 查汇总
r = client.post("/api/reports/ai_metrics/summary",
                json={"paging": "none", "project_codes": ["proj_alpha"]})
data = r.json()["data"]
print(f"rows: {len(data['items'])}")
print(f"totals.ai_req_coverage: {data['extras']['totals_row']['ai_req_coverage']}%")
```

## 11. 模拟数据 (无真实数据源时使用)

V1 当前 **真实数据源未接入**，全部用模拟数据：

```bash
# 容器内：
docker exec ai-metrics-api python -m app.seed.fake_data --days 30 --reset
docker exec ai-metrics-api python -m app.sources.simulate --source all --days 7

# 或直接在 venv：
python -m app.seed.fake_data --days 30 --reset       # 灌一次基础数据
python -m app.sources.simulate --source jira --days 1 # 单源模拟（按 cron 用）
python -m app.sources.simulate --source all --days 7  # 全源 7 天

# 跟 cron 一起用（让 simulate 每小时跑一次当天数据）:
crontab -e
5 * * * * docker exec ai-metrics-api python -m app.sources.simulate --source all --days 1 >> /var/log/ai-metrics-sim.log 2>&1
```

模拟源 4 个：
- `jira`     → req_count / ai_req_count
- `testrail` → new_case_count / ai_case_count / ai_case_adopted
- `gitlab`   → total_code_lines / ai_code_lines / new_script_count / new_script_ai_assisted_count
- `sonar`    → ai_code_accurate_lines

## 12. 错误码与排查

| 现象 | 排查 |
|---|---|
| 422 Unprocessable Entity | 请求 JSON 格式错；看 `detail.loc` |
| 500 Internal Server Error | 看 `docker logs ai-metrics-api`；常见：DATABASE_URL 错 |
| `/api/reports/.../config` 返回空 columns | `dim_domain` 或 `metric_def` 表为空，跑 `seed.fake_data` |
| `/api/.../summary` 行数 = 0 | `ai_metric` 表为空；跑 `app.seed.fake_data` 或 `app.sources.simulate` |
| `/vue/` 报 import 404 | `frontend/vendor/*.js` 缺失；离线部署务必随仓库带这些文件 |
| 跨网压测 timeout | NAT/conntrack 在突发短连接下丢包，改为远端 localhost 内打 ab |
