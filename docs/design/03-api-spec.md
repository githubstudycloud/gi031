# 03 · API Spec —— 自描述配置协议

> **核心理念**：前端**不知道**单个 `report_type` 的字段、筛选、列、下拉源是什么。
> 它只会做三件事：
> 1. 调一次 `/config` 拿"用什么 URL + 传什么参数 + 怎么分页"的元描述。
> 2. 按元描述给筛选区、下拉、列选择器装数据。
> 3. 把用户的筛选/排序/分页/列偏好回传给数据接口和偏好接口。
>
> **新增报表 = 改元数据 + 添新事实表列 + 改生成代码；不改前端。**

约定：
- 所有路径以 `/api` 为前缀。
- 所有响应统一信封：

```jsonc
{
  "code": 0,              // 0=ok；非 0 业务错误码
  "message": "ok",
  "trace_id": "0abc...",  // 排障用
  "data": { ... }         // 真正的负载
}
```

- 分页全部用 `page` (从 1)、`page_size` (默认 50, 上限 500)；返回 `{items, page, page_size, total, has_more}`。
- 鉴权：`Authorization: Bearer <JWT>`，解析出 `user_id`。

---

## 1. 配置接口 `/api/reports/{type}/config`

**GET**。前端在打开报表页时调一次，可缓存到 `report_def.version` 变更为止。

### 1.1 响应数据形状

```jsonc
{
  "meta": {
    "report_type": "daily_sales",
    "name": "每日销售报表",
    "version": 17,                       // 用于前端缓存失效
    "summary_endpoint": "/api/reports/daily_sales/summary",
    "detail_endpoint":  "/api/reports/daily_sales/detail",
    "user_pref_endpoint": "/api/users/me/columns/daily_sales"
  },

  "filters": [
    {
      "code": "business_date",
      "label": "业务日期",
      "kind": "date_range",
      "required": true,
      "default": { "preset": "yesterday" },
      "param": { "from": "date_from", "to": "date_to" }   // 传给 summary/detail 时的字段名
    },
    {
      "code": "region",
      "label": "区域",
      "kind": "hierarchy_dropdown",
      "param": { "value": "region_code" },
      "source": {
        "endpoint": "/api/dropdowns/region_tree",
        "method": "GET",
        "paging": { "enabled": true, "page_size": 50 },
        "sortable_by": ["label", "usage_count", "is_favorite"],
        "default_sort": [{"field": "is_favorite", "dir": "desc"}, {"field": "label", "dir": "asc"}],
        "supports_favorite": true,
        "supports_filter":  true,
        "params_in":  ["parent", "q", "page", "page_size", "sort"]   // 前端能传哪些参数
      }
    },
    {
      "code": "product",
      "label": "产品",
      "kind": "search_dropdown",
      "param": { "value": "product_code" },
      "depends_on": ["region"],          // region 变了，product 重新拉
      "source": {
        "endpoint": "/api/dropdowns/product_search",
        "method": "GET",
        "paging": { "enabled": true, "page_size": 30 },
        "sortable_by": ["label", "sales_rank"],
        "default_sort": [{"field": "sales_rank", "dir": "desc"}],
        "supports_favorite": true,
        "supports_filter":  false,
        "params_in": ["q", "page", "page_size", "sort", "region_code"]   // region_code 由 depends_on 注入
      }
    }
  ],

  "primary_keys": ["region_code", "product_code"],

  "version": {
    "endpoint": "/api/reports/daily_sales/versions",   // 版本选择器
    "param":    "version",
    "default":  "latest",                              // 也可以是具体整数
    "policy":   "latest_per_day"                        // 范围查询时按天取最新
  },

  "primary_view": {                       // 主视图：替代原先的 tabs
    "endpoint": "/api/reports/daily_sales/summary",
    "sortable": true,
    "paging":   { "enabled": true, "default_page_size": 50 },
    "row_favorite": {                     // 行收藏
      "enabled": true,
      "endpoint": "/api/users/me/row_favorites/daily_sales",
      "sort_on_top": true
    }
  },

  // 列定义改成树（2~5 层任意深度）。叶子是真正的列，非叶子是表头分组。
  "columns": {
    "summary": {
      "header_tree": [
        {
          "label": "维度",                          // 1 级
          "code":  "_dim",
          "children": [
            {
              "code": "region_code", "label": "区域", "data_type": "string",
              "is_default_visible": true, "default_order": 1, "default_pinned": "left", "default_width": 140,
              "sortable": true, "row_filterable": true,
              "display": { "kind": "text" }
            },
            {
              "code": "product_code", "label": "产品", "data_type": "string",
              "is_default_visible": true, "default_order": 2,
              "sortable": true, "row_filterable": true, "display": { "kind": "text" }
            }
          ]
        },
        {
          "label": "销售",
          "code":  "_sales",
          "children": [
            {
              "label": "GMV 分渠道",                // 2 级
              "code":  "_sales_gmv",
              "children": [
                {
                  "label": "线上",                  // 3 级（仍是分组）
                  "code":  "_sales_gmv_online",
                  "children": [
                    {
                      "code": "gmv_app", "label": "APP", "data_type": "decimal",
                      "is_default_visible": true,  "default_order": 10,
                      "sortable": true,
                      "drilldown": { "ref": "orders_drilldown" },
                      "display": { "kind": "number", "precision": 0, "thousand": true, "unit": "¥" }
                    },
                    {
                      "code": "gmv_web", "label": "Web", "data_type": "decimal",
                      "is_default_visible": true, "default_order": 11, "sortable": true,
                      "drilldown": { "ref": "orders_drilldown" },
                      "display": { "kind": "number", "precision": 0, "thousand": true, "unit": "¥" }
                    },
                    {
                      "code": "gmv_mp", "label": "小程序", "data_type": "decimal",
                      "is_default_visible": false, "default_order": 12, "sortable": true,
                      "drilldown": { "ref": "orders_drilldown" },
                      "display": { "kind": "number", "precision": 0, "thousand": true, "unit": "¥" }
                    }
                  ]
                },
                {
                  "label": "线下",
                  "code":  "_sales_gmv_offline",
                  "children": [
                    {
                      "code": "gmv_store", "label": "门店", "data_type": "decimal",
                      "is_default_visible": true, "default_order": 20, "sortable": true,
                      "drilldown": { "ref": "orders_drilldown" },
                      "display": { "kind": "number", "precision": 0, "thousand": true, "unit": "¥" }
                    }
                  ]
                }
              ]
            },
            {
              "code": "orders", "label": "订单数", "data_type": "int",
              "is_default_visible": true, "default_order": 30, "sortable": true,
              "drilldown": { "ref": "orders_drilldown" },
              "display": { "kind": "number" }
            },
            {
              "code": "conv_rate", "label": "转化率", "data_type": "decimal",
              "is_default_visible": true, "default_order": 31, "sortable": true,
              "display": { "kind": "percent", "precision": 1 }
            }
          ]
        },
        {
          "label": "库存",
          "code":  "_stock",
          "children": [
            {
              "code": "stock", "label": "库存量", "data_type": "int",
              "is_default_visible": false, "default_order": 40,
              "drilldown": { "ref": "stock_drilldown" },
              "display": { "kind": "number" }
            },
            {
              "code": "safe_days", "label": "安全天数", "data_type": "decimal",
              "is_default_visible": false, "default_order": 41,
              "display": { "kind": "number", "precision": 1 }
            }
          ]
        }
      ]
    }
  },

  // 下钻定义：被 column.drilldown.ref 引用
  "drilldowns": {
    "orders_drilldown": {
      "title": "订单明细 — {row.region_label} / {row.product_code} / {filter.business_date}",
      "endpoint": "/api/reports/daily_sales/detail",
      "method": "GET",
      "param_mapping": {
        "row.region_code":  "region_code",
        "row.product_code": "product_code",
        "filter.business_date.from": "date_from",
        "filter.business_date.to":   "date_to",
        "cell.column": "metric"           // 例：点 gmv_app 会传 metric=gmv_app
      },
      "paging": { "enabled": true, "default_page_size": 50 },
      "sortable": true,
      "supports_row_favorite": false,
      "header_tree": [ /* 同 columns 风格，可独立结构 */ ]
    },
    "stock_drilldown": {
      "title": "库存明细 — {row.region_label}",
      "endpoint": "/api/reports/daily_sales/stock_detail",
      "param_mapping": {
        "row.region_code": "region_code"
      },
      "paging": { "enabled": true, "default_page_size": 50 },
      "header_tree": [ /* ... */ ]
    }
  }
}
```

### 1.2 关键约定

- **`source.params_in`** 是契约：前端只允许传出现在该列表的 query 参数。这能保证下拉服务端可以稳定缓存。
- **`depends_on`**：当父字段值变化，子下拉清空已选，并把父字段值按字段名注入到 `params_in` 中（命名一致）。
- **列状态有两层**：列树叶子上的 `is_default_visible` 是后端默认；`/api/users/me/columns/{report}` 返回个人覆盖。前端 merge 后展示。
- **新加字段** → 在 `field_def` 里 `is_default_visible=false` + `physical_present=false` 时不会影响事实表；事实表迁移完成后切 `true`，前端列选择器自然多一项。
- **header_tree** 渲染规则：DFS 叶子总数 = `<thead>` 最后一行 th 数量；表头总行数 = 树最大深度；非叶子节点的 `colspan` = 其后代叶子数；叶子节点的 `rowspan` = (总深度 - 该叶子深度 + 1)。
- **drilldown 引用**：列上写 `"drilldown": {"ref": "<key>"}`，真正定义在响应根的 `drilldowns` 字典里。这样多个列共享一份下钻配置不重复。 占位符在请求时由前端按 `param_mapping` 替换：`row.*` = 当前数据行字段，`filter.*` = 当前页筛选值，`cell.column` = 被点击的列 code，`cell.value` = 被点击的值。
- **未声明 drilldown 的列**：单元格**不可点击**，鼠标不变手型。这是默认行为，避免管理员误开。

### 1.3 过滤器形态枚举 (`filter.kind`)

不同业务报表的过滤器形态差异极大——有的只要日期，有的要 4 层区域，有的要"日期段+多选 SKU+金额区间"。下表列出协议支持的所有 `kind`，每种都通过同一 `filters` 数组动态声明，前端有一份 dispatch 不动业务代码：

| kind | UI 形态 | `param` 形状 | `default` 形状 | 附加 source 字段 |
|---|---|---|---|---|
| `date_single` | 单个日期选择器 | `{value:"biz_date"}` | `{value:"2026-05-13"}` | — |
| `date_range`  | 起止两个日期 | `{from:"date_from", to:"date_to"}` | `{from:"...", to:"..."}` | — |
| `datetime_single` / `datetime_range` | 同上但带时分 | 同 date | 同 date | — |
| `flat_dropdown` | 平铺单选 | `{value:"region_code"}` | `{value:"CN-31"}` | `paging` `sortable_by` `supports_favorite` `params_in` |
| `hierarchy_dropdown` | 树形单选（懒加载子级） | 同 flat | 同 flat | `max_levels`(1-N) · `select_at_any_depth`(bool) |
| `search_dropdown` | 输入即搜（远程） | 同 flat | 同 flat | `debounce_ms` `min_chars` |
| `multi_select` | 多选 chips（可同时是 flat / hierarchy / search） | `{value:"region_code"}` (数组) | `{value:["CN-31","CN-44"]}` | 同上 + `max_picks` |
| `multi_search` | 远程搜索 + 多选 | 同 multi_select | 同 multi_select | — |
| `text` | 单行文本（精确/模糊看 op） | `{value:"keyword"}` | `{value:""}` | `placeholder` `min_chars` |
| `number_range` | 起止两个数字 | `{from:"min_amt", to:"max_amt"}` | `{from:100, to:9999}` | `min` `max` `step` `unit` |
| `boolean` | 单复选 / 开关 | `{value:"include_test"}` | `{value:false}` | `true_label` `false_label` |
| `enum_radio` | 单行 radio 一组 | `{value:"channel"}` | — | `options:[{value,label}]` |
| `enum_chips` | chips 单选/多选 | 同 enum_radio | — | `options[]`, `multi:bool` |

**关键设计**：
- `hierarchy_dropdown.source.max_levels`：限定树的最大深度。`max_levels=1` 等价于 `flat_dropdown`；`max_levels=4` 表示树最多 4 层（大区/省/市/区）。**到达 max_levels 时该层节点视为叶子**，不再请求子级。
- `hierarchy_dropdown.source.select_at_any_depth`：默认 `false`——只有最深一层（叶子）可被选中；置 `true` 时**每一层都可选**（"选广东"可，"选广东/深圳"也可）。
- 过滤器**全部可选**：`filters` 给空数组也合法，意味着该报表"不需要任何前端筛选，直接出全量当前业务日"。
- 任意 kind 都可写 `depends_on: ["region"]`，父字段值变化时自动重新拉数据并清空当前选择。
- 任意 kind 都可写 `visible_if: { region: { op:"in", value:["CN-31","CN-44"] } }`，按其他过滤器值动态显隐（V1.1，本期占位）。

### 1.4 过滤器配置示例（应对"简单类型"）

**A. 只要日期 + 一层区域**

```jsonc
"filters": [
  { "code":"business_date", "kind":"date_single", "required":true,
    "default":{"value":"2026-05-13"}, "param":{"value":"biz_date"} },
  { "code":"region", "kind":"flat_dropdown",
    "param":{"value":"region_code"},
    "source":{ "endpoint":"/api/dropdowns/region_top",
               "paging":{"enabled":true,"page_size":50},
               "supports_favorite":true, "params_in":["q","page","page_size","sort"] }}
]
```

**B. 时间段 + 4 层区域（可任意层选中）+ 金额区间**

```jsonc
"filters": [
  { "code":"business_date", "kind":"date_range", "required":true,
    "default":{"from":"2026-05-01","to":"2026-05-13"},
    "param":{"from":"date_from","to":"date_to"} },
  { "code":"region", "kind":"hierarchy_dropdown",
    "param":{"value":"region_code"},
    "source":{ "endpoint":"/api/dropdowns/region_tree",
               "paging":{"enabled":true,"page_size":50},
               "max_levels":4, "select_at_any_depth":true,
               "supports_favorite":true, "params_in":["parent","q","page","page_size","sort"]}},
  { "code":"amount", "kind":"number_range",
    "param":{"from":"min_amt","to":"max_amt"}, "default":{"from":1000,"to":null},
    "min":0, "step":100, "unit":"¥" }
]
```

**C. 只要区域，没有日期（按当前最新版直接出数）**

```jsonc
"filters": [
  { "code":"region", "kind":"hierarchy_dropdown",
    "param":{"value":"region_code"},
    "source":{ "endpoint":"/api/dropdowns/region_tree",
               "max_levels":2, "select_at_any_depth":true,
               "supports_favorite":true, "params_in":["parent","q","page","page_size","sort"]}}
]
```

**D. 多选区域 + 多选产品 + 单日**

```jsonc
"filters": [
  { "code":"business_date", "kind":"date_single", "default":{"value":"2026-05-13"},
    "param":{"value":"biz_date"} },
  { "code":"regions",  "kind":"multi_select",
    "param":{"value":"region_code"},
    "source":{ "endpoint":"/api/dropdowns/region_tree", "max_levels":2,
               "supports_favorite":true, "max_picks":8, "params_in":["parent","q","page"] } },
  { "code":"products", "kind":"multi_search",
    "param":{"value":"product_code"}, "depends_on":["regions"],
    "source":{ "endpoint":"/api/dropdowns/product_search",
               "supports_favorite":true, "min_chars":2, "params_in":["q","page","region_code"] } }
]
```

> 这四个示例都只改配置，不改前端代码——前端 `FilterBar` 拿到 `filters[]` 后按 `kind` 派发到对应组件即可（详见 05-frontend-integration §3）。

---

## 2. 数据接口

### 2.1 `GET /api/reports/{type}/summary`

Query 参数（由 config 指定形态）：

| 参数 | 来源 | 说明 |
|---|---|---|
| `date_from`, `date_to` | `filters[business_date].param` | |
| `region_code`          | `filters[region].param.value` | 可重复传多个 |
| `product_code`         | `filters[product].param.value` | |
| `version`              | `version.param` | `latest`(默认) / `<int>` / `all`；对范围查询固定 `latest_per_day` |
| `sort`                 | 标准 | 例 `_row_favorite:desc,gmv:desc,region_code:asc`；`_row_favorite` 是虚拟列，由后端从 `user_row_favorite` 计算 |
| `page`, `page_size`    | 标准分页 | |
| `columns`              | 可选 | 逗号分隔，服务端只返回需要的列 |
| `row_filter`           | 行级筛选 | URL-encoded JSON。详见 §2.1.1 |

#### 2.1.1 行级筛选 `row_filter`

JSON 数组，每项 `{column, op, value}`。前端按列谓词组合：

```jsonc
[
  { "column": "region_code", "op": "in",    "value": ["CN-31","CN-44"] },
  { "column": "gmv",         "op": "gte",   "value": 5000 },
  { "column": "product_code","op": "ilike", "value": "拿铁" }
]
```

可用 op：`eq` `ne` `in` `not_in` `gt` `gte` `lt` `lte` `between` `ilike` `is_null` `is_not_null`。

服务端只对 `field_def.row_filterable=true` 的列接受筛选，其余 400。

#### 2.1.2 响应

```jsonc
{
  "code": 0,
  "data": {
    "version_used": { "scope": "per_day", "min": 3, "max": 5 },  // 范围查询时给出实际使用的版本范围
    "items": [
      {
        "region_code": "CN-31",
        "product_code": "P001",
        "gmv_app": 12345, "gmv_web": 8001, "gmv_store": 6700, "orders": 421,
        "_row_favorite": true,                      // 当前用户是否收藏了这行
        "_row_key": { "region_code":"CN-31","product_code":"P001" },  // 业务主键
        "_audit": { "gmv_app": [{"source":"crm","vno":3,"valid":true}] }
      }
    ],
    "page": 1, "page_size": 50, "total": 248, "has_more": true,
    "extras": {
      "totals": { "gmv_app": 1023456, "orders": 9999 }
    }
  }
}
```

### 2.2 `GET /api/reports/{type}/versions`

版本选择器用。返回某 `business_date` 或日期范围的所有"报表级版本"。

```
GET /api/reports/daily_sales/versions?date_from=2026-05-10&date_to=2026-05-13
```

```jsonc
{
  "code": 0,
  "data": {
    "items": [
      { "business_date":"2026-05-13", "version_no":3, "is_latest":true,
        "status":"active", "generated_at":"...", "generated_by":"scheduler", "note":"" },
      { "business_date":"2026-05-13", "version_no":2, "is_latest":false,
        "status":"superseded", "note":"" },
      { "business_date":"2026-05-13", "version_no":1, "is_latest":false, "status":"superseded" }
    ]
  }
}
```

前端：日期范围下，每天都展示自己的最新版（默认）；用户可手动切某一天到非最新版进行排查。

### 2.3 `POST /api/reports/{type}/drilldown/{ref}`

下钻查询。请求体由 `drilldowns[ref].param_mapping` 在前端预填。

```jsonc
// POST body
{
  "row":  { "region_code": "CN-31", "region_label": "上海", "product_code": "P001" },
  "filter": { "business_date": { "from": "2026-05-13", "to": "2026-05-13" } },
  "cell": { "column": "gmv_app", "value": 12345 },
  "paging": { "page": 1, "page_size": 50 },
  "sort":    "order_amount:desc"
}
```

响应同 §2.1.2，但 `items` 是明细行（订单粒度），且包含 drilldown 的 `header_tree` 已在 `/config` 中预声明。

### 2.4 `GET /api/reports/{type}/lineage`

可选，按 `(business_date, business_key columns)` 返回某一行某列来自哪个 `snapshot_id`，给"溯源"按钮用。

```
GET /api/reports/daily_sales/lineage
   ?business_date=2026-05-13&region_code=CN-31&product_code=P001&column=gmv
```

响应包含选定来源、被丢弃的来源、source_mark 历史。

---

## 3. 下拉框接口 `/api/dropdowns/{key}`

**GET**。

| 参数 | 说明 |
|---|---|
| `parent` | 层级下拉的父 value |
| `q` | 搜索关键字 |
| `page` / `page_size` | 分页 |
| `sort` | 例 `is_favorite:desc,label:asc` |
| `only_favorites` | bool，前端"只看收藏"开关 |
| `filter` | 选项过滤，如 `status:active` |
| `<depends_on_field>` | 由 config.filters[].depends_on 注入的级联字段 |

响应：

```jsonc
{
  "code": 0,
  "data": {
    "items": [
      { "value":"CN-31", "label":"上海", "is_favorite":true, "extra":{"usage_count":120} },
      { "value":"CN-11", "label":"北京", "is_favorite":false, "extra":{"usage_count":210} }
    ],
    "page": 1, "page_size": 50, "total": 31, "has_more": false
  }
}
```

层级下拉（`kind=hierarchy`）扩展：响应中每项可带 `has_children: true`，懒加载子级。

---

## 4. 用户偏好接口

### 4.1 `PUT /api/users/me/favorites/{dropdown_key}`

```jsonc
// body
{ "option_value": "CN-31", "favorited": true }
```

### 4.2 `PUT /api/users/me/row_favorites/{report_type}` —— 数据行收藏

```jsonc
// body
{
  "business_date": "2026-05-13",            // 可选：跨日期收藏传 null
  "business_key":  { "region_code":"CN-31", "product_code":"P001" },
  "favorited": true
}
```

服务端把 `business_key` 规范化（按 PK 字段排序后 hash）后 upsert / delete `user_row_favorite`。
- 查询接口会按 `(user_id, business_date, business_key)` JOIN 出 `_row_favorite=true` 标志。
- 当 `sort` 包含 `_row_favorite:desc`，被收藏的行会被置顶。

### 4.3 `GET /api/users/me/columns/{report_type}?view=summary`

返回个人列覆盖（可能为空表）：

```jsonc
{
  "code": 0,
  "data": {
    "view": "summary",
    "is_personal_default": true,
    "items": [
      { "field_code":"region_code", "is_visible":true,  "order_idx":1, "width":140, "pinned":"left" },
      { "field_code":"gmv",         "is_visible":true,  "order_idx":2 },
      { "field_code":"orders",      "is_visible":false, "order_idx":3 }
    ]
  }
}
```

### 4.4 `PUT /api/users/me/columns/{report_type}`

```jsonc
// body
{
  "view": "summary",
  "is_personal_default": true,
  "items": [ /* 同上 */ ]
}
```

### 4.5 `DELETE /api/users/me/columns/{report_type}?view=summary`

清掉个人覆盖，恢复后端默认。

---

## 5. 管理后台接口（需管理员 JWT scope）

| Method | Path | 描述 |
|---|---|---|
| GET/PUT | `/api/admin/report_types` | 报表类型 CRUD |
| GET/PUT | `/api/admin/fields/{report_type}` | 字段定义 CRUD（**禁止物理 DELETE，只能 set is_hidden / is_deprecated / is_deleted**） |
| GET/PUT | `/api/admin/dropdowns` | 下拉定义 CRUD |
| GET/PUT | `/api/admin/flows/{report_type}` | 流程定义 CRUD（新版本即新行） |
| POST | `/api/admin/flows/{report_type}/run` | 手动触发生成 |
| POST | `/api/admin/source_marks` | 给某次拉取标 valid / invalid |
| POST | `/api/admin/fields/{id}/request-physical-delete` | 提交"物理删除字段"工单（不直接删） |

字段物理删除工作流（重要）：

```
1. Admin set field_def.is_hidden = true              (前端立即看不到)
2. 一段观察期后（保留历史读取）
3. Admin POST /request-physical-delete                (生成审批工单)
4. CI: 检查 flow_def 是否还有 output_columns 含该字段
       检查 column_default / user_column_pref 是否还有引用
       通过则生成 Alembic migration: ALTER TABLE DROP COLUMN
5. 审批人手工 approve → migration apply
6. field_def.physical_present = false, is_deleted = true
```

---

## 6. 错误码段

```
0       OK
4001    参数错误
4031    未登录
4032    无权限
4040    报表类型不存在
4041    字段不存在
4042    下拉 key 不存在
4221    config version 已过期 (前端应重拉 config)
5001    内部错误
5002    上游来源失败
```

错误响应：

```jsonc
{ "code": 4221, "message": "config version expired", "trace_id": "...", "data": {"latest_version": 18} }
```

---

## 7. 缓存/Etag

- `/config` 响应带 `ETag: "<report_type>:<version>"`，前端用 `If-None-Match` 命中返 304。
- 下拉响应也带 ETag，按 `(key, parent, q, sort, page)` 维度缓存到 Redis 60s（个人收藏对结果排序的影响通过 `Vary: X-User-Id` 或 user-id 入键）。

---

## 8. 协议变更策略

- `meta.version`：元数据 (字段/列/下拉) 变更 → +1。
- 字段层面的破坏性变更（删除可见字段）由"工单+迁移"流程兜底。
- API path 变更走 `/api/v2/...`，老 path 保留半年。
