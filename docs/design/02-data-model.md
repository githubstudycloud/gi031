# 02 · Data Model

> 所有表加 `created_at / updated_at / created_by / updated_by`，文中省略。
> 所有"删除"都是软删除：`is_deleted boolean` + `deleted_at`，禁止物理 DELETE，唯有"物理删除字段"需要先改生成代码再发 DDL 工单。

## 1. ER 一览

```
                          ┌──────────────────┐
                          │   report_type    │
                          └────────┬─────────┘
                                   │ 1—N
   ┌──────────────┬────────────────┼─────────────────┬────────────────┐
   ▼              ▼                ▼                 ▼                ▼
┌─────────┐  ┌──────────┐   ┌──────────────┐  ┌──────────────┐ ┌─────────────┐
│column_  │  │ field_   │   │ dropdown_def │  │  flow_def    │ │drilldown_   │
│group    │◀─│ def      │──▶│              │  │              │ │def          │
│(树形)   │  │          │   └──────┬───────┘  └──────┬───────┘ └──────┬──────┘
└─────────┘  └────┬─────┘          │ 1—N             │ 1—N            │
                  │ 1—N                                                │
                  ▼                                                    │
            ┌────────────────┐                                         │
            │ column_default │                                         │
            └────────┬───────┘                                         │
                     │                                                 │
                     ▼                                                 │
            ┌────────────────┐                                         │
            │ user_column_   │   ┌──────────────────┐                  │
            │ pref           │   │ user_favorite    │  (per dropdown)  │
            └────────────────┘   │ user_row_        │  (per fact row)  │
                                 │ favorite         │                  │
                                 └──────────────────┘                  │
                                                                       │
   ┌───────────────────────────────────────────────────────────────────▼┐
   │  事实 / 快照 / 版本 / 来源标记 / 下钻（按 report_type 动态生成）   │
   │  report_fact_<T>          :  当日某版本 → 一行 = (date, dims, ver) │
   │  report_fact_version      :  每日的版本号注册表 + 是否当前默认     │
   │  report_snapshot_<T>      :  按 (业务日期, 来源, snap版本) 切片    │
   │  source_mark              :  (报表, 业务日期, 来源, snap版) valid? │
   │  drilldown_def 引用的明细表：通常是源表/事件表，按场景定           │
   └────────────────────────────────────────────────────────────────────┘
```

**两类"版本"不要混**：
- **报表版本** `report_fact_version` —— merger 跑一次产生一个，同日可有多版（如 02:00 自动跑、09:00 人工补抓后再跑）。默认查询取每日 **最新版**。
- **快照版本** `report_snapshot_<T>.version_no` —— 单个数据来源当日的第 N 次拉取，merger 用 `source_mark` 选 valid 的最大版本去合并。前端不直接感知，只用作"溯源"展示。

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
| `column_group_id` FK | bigint | 指向 `column_group` 树的某节点，**决定列在表头中所处的分类路径** |
| `sort_order` | int | 同组内同级顺序 |
| `is_hidden` | bool | **软隐藏**：列选择器中不出现，已有事实表列**保留**不丢数据 |
| `is_deprecated` | bool | 标记为不再写入，但历史值保留 |
| `physical_present` | bool | 当前事实表是否实际包含此列（删除前必须先把生成代码切走才能置 false） |
| `drilldown_def_id` FK | bigint | nullable；非空表示该单元格可点击下钻，引用 `drilldown_def` |
| `row_filterable` | bool | 该列在数据表格中是否允许行级筛选（按值匹配/范围/搜索） |

**唯一约束**: `(report_type, code)` `WHERE NOT is_deleted`。

### 2.3 `column_group` —— 多层表头分组树

支持 **2~5 层** 任意深度的分类结构。叶子由 `field_def.column_group_id` 指向；非叶子节点用于 `<thead>` 中的合并表头 (colspan)。

| 列 | 类型 | 说明 |
|---|---|---|
| `id` PK | bigserial | |
| `report_type` FK | text | |
| `view` | enum(`summary`,`detail_modal`) | 同字段在不同视图可挂不同分组路径 |
| `parent_id` FK | bigint | nullable；空表示根 |
| `code` | text | 分组编码，调试 / 配置引用用 |
| `label` | text | 表头显示文字 |
| `sort_order` | int | 同 parent 下兄弟节点的顺序 |
| `depth` | int | 计算列：根=1；叶子=N（最大 5）；用于约束最大深度与查询 |
| `is_collapsible` | bool | 列选择器中是否允许整组折叠/展开 |

**约束**：
- 同一 `(report_type, view)` 下，`depth <= 5`。
- 叶子节点（即 `field_def.column_group_id` 指向的那一行）不能再有子节点。
- `parent_id` 不能成环（DB 触发器或应用层校验）。

**查询模式**：组装 `/config` 响应时一次 RECURSIVE CTE 拿整棵树，再把叶子节点替换成 `field_def + column_default` 的合并对象 → 输出为 `header_tree`（详见 03-api-spec §1.1）。

### 2.4 `drilldown_def` —— 单元格下钻定义

某个数值列（如"订单数"）支持点击数字弹出弹框，展示明细行的来源 / 字段 / 查询参数。

| 列 | 类型 | 说明 |
|---|---|---|
| `id` PK | bigserial | |
| `code` | text | 唯一编码，例 `daily_sales.orders_to_orders` |
| `title` | text | 弹框标题，例 "订单明细 — {region_label} / {product_code} / {business_date}" |
| `data_source` | enum(`sql`,`api`) | |
| `source_config` | jsonb | 与 `dropdown_def.source_config` 类似；可用 `{row.<col>}`、`{filter.<code>}`、`{cell.value}`、`{business_date}` 占位符 |
| `column_group_view` | text | 用 `column_group` 中 `view='detail_modal'` 的某子树渲染弹框内的表头 |
| `paging` | jsonb | `{enabled:true, page_size:50}` |
| `sortable_by` | text[] | |
| `default_sort` | jsonb | |
| `supports_row_favorite` | bool | 弹框内的明细行也可收藏（默认 false） |

> 一个下钻定义可被多个字段引用（多列共用一份弹框 schema）。

### 2.5 `column_default` —— 后端"默认列展示"

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

### 2.6 `dropdown_def` —— 筛选/下拉定义

| 列 | 类型 | 说明 |
|---|---|---|
| `key` PK | text | 全局唯一，例 `region_tree`、`product_search` |
| `report_type` FK | text | 归属（一个下拉可能跨报表复用：本字段为可空，nullable 表示通用） |
| `kind` | enum(`flat_dropdown`,`hierarchy_dropdown`,`search_dropdown`,`multi_select`,`multi_search`,`date_single`,`date_range`,`datetime_single`,`datetime_range`,`text`,`number_range`,`boolean`,`enum_radio`,`enum_chips`) | 全部 14 种过滤器形态，详见 03-api §1.3 |
| `data_source` | enum(`static`,`sql`,`api`,`derived`) | 选项来自哪里 (对枚举类 / 下拉类有效) |
| `source_config` | jsonb | 由 `data_source` 决定形态（见下） |
| `paging` | jsonb | `{"enabled":true,"page_size":50}` |
| `sortable_by` | jsonb | `["label","usage_count","is_favorite"]` |
| `default_sort` | jsonb | `[{"field":"is_favorite","dir":"desc"},{"field":"label","dir":"asc"}]` |
| `supports_favorite` | bool | |
| `supports_filter` | bool | 是否在选项上再开 filter（如"只看启用的"） |
| `parent_key` | text | 层级下拉时，父级的 dropdown_def.key |
| `max_levels` | int | 层级下拉的最大深度。`1` 等价于 flat；超过则按需懒加载。**到达 max_levels 的节点视为叶子** |
| `select_at_any_depth` | bool | 层级下拉中"非叶子节点是否可被选中"；默认 `false` |
| `max_picks` | int | multi_select / multi_search 的最大可选数；空表示不限 |
| `min_chars` | int | search_dropdown / multi_search 触发查询的最小字符数 |

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

### 2.7 `dropdown_option` —— 静态选项缓存（可选）

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

### 3.3 `user_row_favorite` —— 数据行收藏

针对**事实表中的某一行**收藏（不是下拉选项）；在表格中支持"收藏置顶"的排序行为。

| 列 | 类型 | 说明 |
|---|---|---|
| `user_id` | text | |
| `report_type` | text | |
| `business_date` | date | 与具体日期挂钩（避免歧义）；如果是"跨日期持续收藏"用 `NULL` |
| `business_key` | jsonb | 该报表的主键字段值，例 `{"region_code":"CN-31","product_code":"P001"}` |
| `favorited_at` | timestamptz | |

`PK = (user_id, report_type, business_date, business_key)`，`business_key` 用 jsonb 等价比较（或抽 hash 列）。

排序：API 层支持 `sort=is_row_favorite:desc,...`；服务端在返回行时根据 `user_row_favorite` 给每行加 `_is_favorite: bool`。

### 3.4 `user_filter_preset` —— 用户保存的筛选条件组合（V1.1）

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

## 5. 事实 / 版本 / 快照 / 来源标记（按报表类型）

> 注意：`report_fact_<T>` 与 `report_snapshot_<T>` 的列结构由 `field_def.physical_present = true` 的行**生成 + Alembic 迁移**得到。**修改字段不会写直 DDL；走 generator → review → migration → apply** 流程，保证可审计。

### 5.1 `report_fact_<T>` —— 支持每日多版本

每日可以有多个"最终版"——例如 02:00 自动跑出 v1，09:00 人工补抓后 merger 重跑出 v2。**所有版本都保留**，前端默认查询每日最新版。

```sql
CREATE TABLE report_fact_daily_sales (
  business_date date NOT NULL,
  version_no    int  NOT NULL,             -- ← 报表级版本，与 snapshot 的 source-version 不同
  region_code   text NOT NULL,
  product_code  text NOT NULL,
  -- 度量列：从 field_def 生成
  gmv           numeric(18,2),
  orders        bigint,
  -- ...
  -- 审计列：列粒度告诉你"这一格"是从哪个 snapshot 来的
  source_snapshot_ids jsonb,   -- {"gmv": 1234, "orders": 1235}
  generated_at  timestamptz NOT NULL,
  PRIMARY KEY (business_date, version_no, region_code, product_code)
);
CREATE INDEX ON report_fact_daily_sales (business_date, version_no);
```

### 5.2 `report_fact_version` —— 每日版本注册表（公共表）

| 列 | 类型 | 说明 |
|---|---|---|
| `report_type` | text | |
| `business_date` | date | |
| `version_no` | int | 当日第 N 个最终版 |
| `flow_run_id` FK | bigint | 哪次执行产出的 |
| `status` | enum(`active`,`superseded`,`rolled_back`) | active=可被查；superseded=被新版本替换；rolled_back=人工回退 |
| `generated_at` | timestamptz | |
| `generated_by` | text | "scheduler" / "manual:vickroy" / "reconcile" |
| `note` | text | 例 "补抓 wms 后重算" |

`PK = (report_type, business_date, version_no)`。

**默认"取每日最新版"** 的实现：服务端用窗口函数解析，把查询折算成
```sql
SELECT *
FROM report_fact_daily_sales f
JOIN (
  SELECT business_date, MAX(version_no) AS v
  FROM report_fact_version
  WHERE report_type = 'daily_sales'
    AND status = 'active'
    AND business_date BETWEEN :from AND :to
  GROUP BY business_date
) latest ON f.business_date = latest.business_date AND f.version_no = latest.v
WHERE f.business_date BETWEEN :from AND :to
  AND ...
```

API 层接受 `version` 参数（详见 03-api-spec §2.1）：
- `latest`（默认）→ 上述窗口函数
- `<int>` → 指定版本号；对范围查询时报错或要求点日期
- `all` → 不做版本过滤，返回所有版本（前端做对比视图，本期不实现）

### 5.3 `report_snapshot_<T>`

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

### 5.4 `source_mark` —— 人工标记快照有效性（公共表）

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
