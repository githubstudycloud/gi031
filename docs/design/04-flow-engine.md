# 04 · Flow Engine —— 生成服务

## 1. 目标回顾

- 一类报表的生成过程是一个 **DAG**：多个 extract / transform 节点，无依赖关系的节点 **并发执行**，所有抓数完成后由一个 merger 节点收口。
- 同一列字段可能由 **多个来源**提供，每次执行**所有候选来源**都拉一份，得到多个 snapshot。
- 同一 (业务日期, 来源) **可能跑多版**（重跑、迟到补抓）。
- 人工可对 (业务日期, 来源, 版本) 标 `valid / invalid`。
- 合并器按 `merge_rule` 在所有"valid 候选"中按列粒度挑出最终值，写入事实表。

## 2. DAG 示意

下面是 `daily_sales` 报表的示例流程（来自 flow_def + flow_step + flow_edge）：

```
              ┌───────────────────┐
              │  init (no-op)      │
              │  business_date 上下文 │
              └─────┬─────────────┘
                    │
       ┌────────────┼─────────────┬───────────────┐
       ▼            ▼             ▼               ▼
 ┌───────────┐ ┌──────────┐ ┌───────────┐ ┌──────────────┐
 │ extract:  │ │ extract: │ │ extract:  │ │ extract:     │
 │ crm_api   │ │ erp_db   │ │ wms_api   │ │ manual_csv   │
 │ → gmv     │ │ → orders │ │ → stock   │ │ → stock(备)  │
 │   orders  │ │   refund │ │           │ │              │
 └─────┬─────┘ └────┬─────┘ └─────┬─────┘ └─────┬────────┘
       │            │             │             │
       └────────────┴──────┬──────┴─────────────┘
                           ▼
                  ┌────────────────────┐
                  │  validate (可选)   │
                  │  字段空率/数量阈值 │
                  └────────┬───────────┘
                           ▼
                  ┌────────────────────┐
                  │  merger (chord 回调)│
                  │  按列挑版本 → fact │
                  └────────────────────┘
```

实现层面：每个 `extract` 节点是一个 Celery task；同层无依赖的节点用 `group` 并发；最后用 `chord` 等所有分支完成后调用 merger。

## 3. 步骤定义 `flow_step.source_config`

按 `source_type` 给不同形态：

### 3.1 HTTP API

```jsonc
{
  "endpoint": "https://crm.example.com/api/sales",
  "method": "GET",
  "params": {
    "date": "{business_date}",         // 占位符运行时替换
    "page_size": 1000
  },
  "headers": { "Authorization": "Bearer {secret:crm_token}" },
  "auth_secret": "crm_token",          // 从 secrets 服务取
  "pagination": {
    "kind": "page",
    "page_field": "page",
    "size_field": "page_size",
    "items_path": "data.items",
    "has_more_path": "data.has_more"
  },
  "rate_limit": { "rps": 5, "concurrency": 2 },
  "field_mapping": {
    "gmv":     "amount",
    "orders":  "order_count",
    "region_code":  "regionCode",
    "product_code": "productCode"
  },
  "primary_keys": ["region_code", "product_code"]
}
```

### 3.2 SQL

```jsonc
{
  "dsn_alias": "erp_readonly",
  "sql": "SELECT region_code, product_code, order_count AS orders, refund_amount AS refund FROM dws.orders WHERE biz_date = :business_date",
  "params": { "business_date": "{business_date}" },
  "field_mapping": {
    "orders": "orders",
    "refund": "refund"
  },
  "primary_keys": ["region_code", "product_code"]
}
```

### 3.3 File

```jsonc
{
  "kind": "csv",
  "path_template": "s3://bucket/manual/{business_date}/stock.csv",
  "encoding": "utf-8-sig",
  "header": true,
  "field_mapping": { "stock": "stock_qty" },
  "primary_keys": ["region_code", "product_code"],
  "fail_if_missing": false              // 允许缺文件
}
```

### 3.4 Compute / Transform

```jsonc
{
  "engine": "python",
  "function_path": "services.generation.app.computes.gmv_qoq",
  "inputs": ["gmv"],
  "outputs": ["gmv_qoq"]
}
```

## 4. 运行时数据流

```
orchestrator.run_report(report_type, business_date):
  1. flow_def = load_active(report_type)
  2. flow_run = create(flow_def, business_date, status=running)
  3. dag = parse(flow_def, business_date)
  4. for each layer in topo_layers(dag):
        group_results = group(
            branch.delay(step, flow_run.id) for step in layer
        ).get()
  5. merger.delay(flow_run.id) (chord 回调)
  6. flow_run.status = success | partial | failed
```

但实际 Celery 实现更优雅：

```python
canvas = chord(
    group(branch.s(step_id, flow_run_id) for step_id in extract_layer),
    merger.s(flow_run_id),
)
canvas.apply_async()
```

### 4.1 branch task 伪代码

```python
@app.task(bind=True, autoretry_for=(TransientError,), retry_backoff=True, retry_kwargs={"max_retries": 3})
def branch(self, step_id: int, flow_run_id: int):
    step = repo.get_step(step_id)
    ctx  = repo.get_flow_run(flow_run_id)
    adapter = SourceRegistry.get(step.source_type)
    rows = adapter.pull(step.source_config, context=ctx)

    # 求当日同来源最大 version_no
    version_no = repo.next_version(report_type=ctx.report_type,
                                   business_date=ctx.business_date,
                                   source_code=step.code)
    snapshot_id = repo.write_snapshot(
        report_type=ctx.report_type,
        business_date=ctx.business_date,
        source_code=step.code,
        version_no=version_no,
        rows=rows,
    )
    return {"step": step.code, "snapshot_id": snapshot_id, "row_count": len(rows)}
```

要点：
- **幂等**：同 (report_type, business_date, source_code, version_no) 重复写会冲突；用 `INSERT … ON CONFLICT` 或先 SELECT 锁定 version_no。
- 重试由 Celery 处理，但 **不应改 version_no**（同次任务的两次执行写同一 version）。
- 拉取数据用 `row_hash` 去重，避免分页抖动重复。

### 4.2 merger task 伪代码

```python
@app.task
def merger(branch_results: list[dict], flow_run_id: int):
    ctx = repo.get_flow_run(flow_run_id)
    rule = ctx.flow_def.merge_rule
    snapshots = repo.list_snapshots(ctx.report_type, ctx.business_date)
    marks = repo.list_source_marks(ctx.report_type, ctx.business_date)

    # 1. 按 (source_code) 过滤出最高有效版本
    chosen_by_source = pick_versions(snapshots, marks, rule["version_pick"])

    # 2. 按主键合并行（不同 source 提供不同列）
    merged = {}
    audit  = {}
    for src in rule["column_sources"]:  # 注意：迭代列
        for col, sources in rule["column_sources"].items():
            for source_code in sources:
                snap = chosen_by_source.get(source_code)
                if not snap: continue
                for row in snap.payload:
                    pk = tuple(row[k] for k in ctx.primary_keys)
                    if pk not in merged: merged[pk] = {}
                    if col in row and col not in merged[pk]:
                        merged[pk][col] = row[col]
                        audit.setdefault(pk, {})[col] = snap.id
                if col in merged.get(pk, {}):
                    break   # 找到了，按优先级 break

    # 3. fallback
    apply_fallback(merged, rule, ctx)

    # 4. upsert 到 fact 表
    repo.upsert_fact(ctx.report_type, ctx.business_date, merged, audit)
    repo.finish_run(flow_run_id, status_for(merged, branch_results))
```

## 5. 来源标记 (source_mark) 触发的重算

```
POST /api/admin/source_marks
  body: {report_type, business_date, source_code, version_no, is_valid:false, reason:"上游回滚"}

→ 后端事务：
  1. 写 source_mark
  2. 入队 reconcile.delay(report_type, business_date)
     - 不重新抓数，仅再跑 merger 重算 fact
  3. 事实表行的 source_snapshot_ids 自动反映新选择
```

合并器**总是基于当前 source_mark 状态**重算，所以"换一个版本生效"= 改 mark + 重跑 merger，不需要重抓数。

## 6. 失败 / 重试 / 局部成功语义

| 场景 | 行为 |
|---|---|
| 单分支 retry 内成功 | 视为该分支 success；继续 |
| 单分支耗尽 retry | 该分支记 failed；继续别的分支；最终 `flow_run.status = partial` |
| merger 见到某来源缺位 | 按 `merge_rule.fallback` 处理（skip / carry_over / null） |
| merger 自身失败 | flow_run = failed；事实表不变；可手工触发 reconcile |
| 同日重跑（人工触发） | version_no 累加；merger 重新挑选；fact 被覆盖 |

## 7. 并发模型

- **节点并发**：同 DAG 层用 `group`，每个节点一个 worker 任务。
- **批内并发**：单节点内部如有"多页 / 多分片"，由 adapter 内部用 asyncio / 线程池实现（不开 task 子任务，避免管理复杂）。
- **跨报表并发**：不同 report_type 完全独立，按队列隔离（`report-high`、`report-low`）。
- 限流：API 来源在 adapter 层用 `aiolimiter` 或 `tenacity`，DB 来源在 dsn 池层限并发。

## 8. 调度与触发

- **定时**：`flow_def.schedule_cron` → Celery Beat。
- **手动**：`POST /api/admin/flows/{type}/run?business_date=YYYY-MM-DD`。
- **依赖触发**（后续）：上游 ETL 完成事件 → 内部触发；先用定时 + 重试。

## 9. 可观测性

- 每个 task 产生结构化日志：`{trace_id, flow_run_id, step, source_code, version_no, row_count, duration_ms, status}`。
- Prometheus 指标：
  - `flow_run_duration_seconds{report_type,status}`
  - `branch_row_count{report_type,source_code}`
  - `merger_fallback_total{report_type,reason}`
  - `source_mark_invalid_total{report_type,source_code}`
- 失败发到 Slack/IM；连续 3 天某来源失败发告警工单。
