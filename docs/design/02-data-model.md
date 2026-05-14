# 02 · Data Model

> 所有表加 `created_at / updated_at / created_by / updated_by`，文中省略。
> 所有"删除"都是软删除：`is_deleted boolean` + `deleted_at`，禁止物理 DELETE，唯有"物理删除字段"需要先改生成代码再发 DDL 工单。

## 1. ER 一览

```
                        ┌──────────────────┐
                        │   report_type    │   报表类型登记
                        └────────┬─────────┘
                                 │ 1—N
            ┌────────────────────┼────────────────────────────┐
            │                    │                            │
            ▼                    ▼                            ▼
   ┌────────────────┐   ┌─────────────────┐         ┌──────────────────┐
   │  field_def     │   │  dropdown_def   │         │     flow_def     │
   │  字段定义      │   │  下拉/筛选定义  │         │   生成流程 DAG   │
   └────────┬───────┘   └────────┬────────┘         └────────┬─────────┘
            │ 1—N                │ 1—N                       │ 1—N
            ▼                    ▼                           ▼
   ┌────────────────┐   ┌─────────────────┐         ┌──────────────────┐
   │ column_default │   │ dropdown_option │         │ flow_step / edge │
   │ 默认列状态     │   │ (可选静态)      │         └────────┬─────────┘
   └────────┬───────┘   └─────────────────┘                  │
            │                                                ▼
            ▼                                       ┌──────────────────┐
   ┌────────────────┐    ┌───────────────────┐      │     flow_run     │
   │ user_column_   │    │ user_favorite     │      │   执行记录       │
   │ pref (per uid) │    │ (per uid + key)   │      └────────┬─────────┘
   └────────────────┘    └───────────────────┘               │
                                                              ▼
   ┌────────────────────────────────────────────────────────────────┐
   │  事实 / 快照 / 来源标记（按 report_type 动态生成的表）         │
   │  report_fact_<T>     :  当日最终版                             │
   │  report_snapshot_<T> :  按 (业务日期, 来源, 版本) 切片         │
   │  source_mark         :  (报表, 业务日期, 来源, 版本) → valid?  │
   └────────────────────────────────────────────────────────────────┘
```

## 2. 元数据表（公共，所有报表共用）

### 2.1 `report_type`

| 列 | 类型 | 说明 |
|---|---|---|
| `code` PK | text | 业务标识，如 `daily_sales` |
| `name` | text | 展示名 |
| `version` | int | 配置版本号，元数据变更 +1，用于前端缓存失效 |
| `summary_table` | text | 对应的事实/视图表名，例 `report_fact_daily_sales` |
| `detail_table` | text | 详情表名（可与 summary 共用） |
| `is_active` | bool | 是否对外可见 |
| `description` | text | |

### 2.2 `field_def` —— 字段元数据

| 列 | 类型 | 说明 |
|---|---|---|
| `id` PK | bigserial | |
| `report_type` FK | text | 所属报表 |
| `code` | text | 字段编码，对应数据库列名 |
| `label` | text | 展示名 |
| `data_type` | enum(`string`,`int`,`decimal`,`date`,`datetime`,`bool`,`json`) | |
| `unit` | text | 单位（货币/百分比等） |
| `display_format` | jsonb | 前端展示规则：`{"kind":"number","precision":2,"thousand":true}` 等 |
| `is_dimension` | bool | 是否是维度（参与分组/筛选） |
| `is_measure` | bool | 是否是度量 |
| `group_code` | text | 列分组名，用于列选择器分组展示 |
| `sort_order` | int | 同组内顺序 |
| `is_hidden` | bool | **软隐藏**：列选择器中不出现，已有事实表列**保留**不丢数据 |
| `is_deprecated` | bool | 标记为不再写入，但历史值保留 |
| `physical_present` | bool | 当前事实表是否实际包含此列（删除前必须先把生成代码切走才能置 false） |

**唯一约束**: `(report_type, code)` `WHERE NOT is_deleted`。

### 2.3 `column_default` —— 后端"默认列展示"

| 列 | 类型 | 说明 |
|---|---|---|
| `report_type` FK | text | |
| `field_code` FK | text | |
| `view` | enum(`summary`,`detail`) | 哪个表的默认 |
| `is_default_visible` | bool | 默认是否展示 |
| `default_order` | int | 默认顺序 |
| `default_width` | int | 像素，可空 |
| `default_pinned` | enum(`none`,`left`,`right`) | |

`PK = (report_type, field_code, view)`。

### 2.4 `dropdown_def` —— 筛选/下拉定义

| 列 | 类型 | 说明 |
|---|---|---|
| `key` PK | text | 全局唯一，例 `region_tree`、`product_search` |
| `report_type` FK | text | 归属（一个下拉可能跨报表复用：本字段为可空，nullable 表示通用） |
| `kind` | enum(`flat`,`hierarchy`,`search`,`date_range`) | |
| `data_source` | enum(`static`,`sql`,`api`,`derived`) | 选项来自哪里 |
| `source_config` | jsonb | 由 `data_source` 决定形态（见下） |
| `paging` | jsonb | `{"enabled":true,"page_size":50}` |
| `sortable_by` | jsonb | `["label","usage_count","is_favorite"]` |
| `default_sort` | jsonb | `[{"field":"is_favorite","dir":"desc"},{"field":"label","dir":"asc"}]` |
| `supports_favorite` | bool | |
| `supports_filter` | bool | 是否在选项上再开 filter（如"只看启用的"） |
| `parent_key` | text | 层级下拉时，父级的 dropdown_def.key |

**`source_config` 形态举例**：

```jsonc
// SQL 数据源
{
  "sql": "SELECT code AS value, name AS label, parent_code FROM dim_region WHERE :q IS NULL OR name ILIKE :q || '%'",
  "params": ["q", "parent"],
  "label_field": "label",
  "value_field": "value"
}

// API 数据源（拉外部）
{
  "endpoint": "https://internal-svc.example.com/regions",
  "method": "GET",
  "query_template": {"keyword": "{q}", "parent": "{parent}"},
  "auth": "service_token_x",
  "response_path": "data.items",
  "label_field": "name",
  "value_field": "code"
}

// 静态枚举
{
  "options": [{"value":"Y","label":"是"},{"value":"N","label":"否"}]
}
```

### 2.5 `dropdown_option` —— 静态选项缓存（可选）

如果 `dropdown_def.data_source = static`，选项直接存在 `source_config.options`。如果是 SQL/API，**不在此表落地**（按需查），但可以加一张 `dropdown_option_cache` 由 Redis/物化视图扛量。

## 3. 用户偏好表

### 3.1 `user_favorite`

| 列 | 类型 |
|---|---|
| `user_id` | text |
| `dropdown_key` FK | text |
| `option_value` | text |
| `favorited_at` | timestamptz |

`PK = (user_id, dropdown_key, option_value)`。

### 3.2 `user_column_pref` —— 个人列定制

存"个人覆盖"，没记录就回退到 `column_default`。

| 列 | 类型 | 说明 |
|---|---|---|
| `user_id` | text | |
| `report_type` | text | |
| `view` | enum(`summary`,`detail`) | |
| `field_code` | text | |
| `is_visible` | bool | |
| `order_idx` | int | |
| `width` | int | nullable |
| `pinned` | enum(`none`,`left`,`right`) | |
| `is_personal_default` | bool | 标记一组为"我的默认" |

`PK = (user_id, report_type, view, field_code)`。

> 个人定制 ≠ 后端默认：前端从 config 拿到后端默认，再 merge 用户偏好。"恢复默认"= 清掉 user_column_pref 中该 (user, report, view) 的所有行。

### 3.3 `user_filter_preset` —— 用户保存的筛选条件组合（V1.1）

预留表，本期不实现。

## 4. 流程定义表

### 4.1 `flow_def`

| 列 | 类型 | 说明 |
|---|---|---|
| `id` PK | bigserial | |
| `report_type` FK | text | |
| `version` | int | flow_def 版本号；新版本不动旧版本 |
| `is_active` | bool | 同一 report_type 仅一个 active |
| `schedule_cron` | text | nullable，给 beat 用 |
| `merge_rule` | jsonb | 详见 §6 |

### 4.2 `flow_step`

每一行是一个 DAG 节点。

| 列 | 类型 | 说明 |
|---|---|---|
| `id` PK | bigserial | |
| `flow_def_id` FK | bigint | |
| `code` | text | 节点编码，DAG 内唯一 |
| `kind` | enum(`extract`,`transform`,`aggregate`,`validate`) | |
| `source_type` | enum(`http`,`sql`,`file`,`compute`) | |
| `source_config` | jsonb | 适配器读它执行；详见 04-flow-engine |
| `output_columns` | text[] | 本步骤写入哪些 field_def.code |
| `retry_policy` | jsonb | `{"max":3,"backoff":"exponential"}` |
| `timeout_seconds` | int | |

### 4.3 `flow_edge`

| 列 | 类型 |
|---|---|
| `flow_def_id` FK | bigint |
| `from_step` | text |
| `to_step` | text |

无边即并发分支；最后一个收口节点是隐式的 `__merge__`（在 merger 里实现）。

### 4.4 `flow_run`

| 列 | 类型 |
|---|---|
| `id` PK | bigserial |
| `flow_def_id` FK | bigint |
| `business_date` | date |
| `status` | enum(`pending`,`running`,`success`,`partial`,`failed`) |
| `started_at` / `finished_at` | timestamptz |
| `triggered_by` | text |
| `summary` | jsonb (各分支状态) |

### 4.5 `flow_step_run`

每个分支一行；用来失败重跑、看哪个来源失败。

## 5. 事实 / 快照 / 来源标记（按报表类型）

> 注意：`report_fact_<T>` 与 `report_snapshot_<T>` 的列结构由 `field_def.physical_present = true` 的行**生成 + Alembic 迁移**得到。**修改字段不会写直 DDL；走 generator → review → migration → apply** 流程，保证可审计。

### 5.1 `report_fact_<T>`

当日最终版，**1 行 = 1 个业务主键**（如一个区域 × 一个产品 × 一天）。

```sql
CREATE TABLE report_fact_daily_sales (
  business_date date NOT NULL,
  region_code   text NOT NULL,
  product_code  text NOT NULL,
  -- 度量列：从 field_def 生成
  gmv           numeric(18,2),
  orders        bigint,
  -- ...
  -- 审计列：列粒度告诉你"这一格"是从哪个版本来的
  source_snapshot_ids jsonb,  -- {"gmv": 1234, "orders": 1235}
  generated_at  timestamptz NOT NULL,
  PRIMARY KEY (business_date, region_code, product_code)
);
```

`source_snapshot_ids` 让你点击单元格能溯源到具体的 `report_snapshot_<T>.id`。

### 5.2 `report_snapshot_<T>`

每个 (业务日期, 来源, 版本) 一行。

```sql
CREATE TABLE report_snapshot_daily_sales (
  id            bigserial PRIMARY KEY,
  business_date date NOT NULL,
  source_code   text NOT NULL,            -- 'crm_api' / 'erp_db' / ...
  version_no    int  NOT NULL,            -- 同一来源当日的第几次拉取
  pulled_at     timestamptz NOT NULL,
  -- 这一来源**能提供**的列（field_def.code 的子集）
  payload       jsonb NOT NULL,           -- {region_code, product_code, gmv, orders, ...}
  row_hash      bytea,                    -- 幂等/去重
  UNIQUE (business_date, source_code, version_no)
);
CREATE INDEX ON report_snapshot_daily_sales (business_date, source_code);
```

> `payload` 用 JSONB 而不是宽表列，是因为不同来源给的列不一样，避免 NULL 海洋。事实表才落规范化列。

### 5.3 `source_mark` —— 人工标记有效性（公共表）

| 列 | 类型 |
|---|---|
| `report_type` | text |
| `business_date` | date |
| `source_code` | text |
| `version_no` | int |
| `is_valid` | bool |
| `reason` | text |
| `marked_by` | text |
| `marked_at` | timestamptz |

`PK = (report_type, business_date, source_code, version_no)`。

**默认行为**：没记录 → 视为 `is_valid = true`。

## 6. 合并规则 `flow_def.merge_rule`

JSONB，描述某报表"每一列从哪里来 + 如何挑版本"：

```jsonc
{
  "column_sources": {
    "gmv":     ["crm_api", "erp_db"],     // 列出可候选来源，按优先级
    "orders":  ["erp_db"],
    "stock":   ["wms_api", "manual_csv"]
  },
  "version_pick": "latest_valid",         // latest_valid | latest_any | earliest_valid
  "fallback":    "skip_column",           // skip_column | keep_yesterday | null
  "conflict":    "by_priority"            // by_priority | error | manual_review
}
```

- `latest_valid`：在 source_mark 中 `is_valid = true` 的最大 `version_no`。
- `keep_yesterday`：若今日所有候选都被标记无效，沿用昨日 fact 的值（在审计列上明确标记 `carried_over: true`）。
- `manual_review`：合并器写入"待人工裁决"队列，事实表该单元格留空，前端提示。

## 7. 索引与约束要点

- 所有事实表按 `(business_date, 主维度)` 建组合索引。
- `report_snapshot_<T> (business_date, source_code)` 必须有索引（合并器扫描会用）。
- `field_def` 的 `physical_present = false` 但 `is_deleted = true` 的行**保留** ——审计需要。
- `user_column_pref` 加 `(user_id, report_type, view)` 复合索引，列出个人偏好快。
