# 设计 · AI 测试度量看板

## 1. 业务目标

度量 AI 在两个测试环节的渗透：
- **测试设计**：AI 提需求 / AI 生成用例的覆盖率与采纳率
- **测试脚本生成**：AI 生成代码的占比、入库行数、准确率

## 2. 指标口径

| code | label | category | unit | data_type | agg_method | computed_formula | drilldown |
|------|------|----------|------|-----------|------------|------------------|-----------|
| req_count                | 需求个数               | 测试设计 | 个 | int     | sum      | — | ✅ |
| ai_req_count             | AI 需求个数            | 测试设计 | 个 | int     | sum      | — | ✅ |
| ai_req_coverage          | AI 涉及需求覆盖率       | 测试设计 | % | percent | computed | `ai_req_count/req_count*100` | — |
| new_case_count           | 新增用例个数            | 测试设计 | 个 | int     | sum      | — | ✅ |
| ai_case_count            | AI 用例个数             | 测试设计 | 个 | int     | sum      | — | ✅ |
| ai_case_ratio            | 测试用例 AI 生成占比     | 测试设计 | % | percent | computed | `ai_case_count/new_case_count*100` | — |
| ai_case_adoption_rate    | AI 生成用例采纳率        | 测试设计 | % | percent | weighted_avg | weight=`ai_case_count` | — |
| ai_script_code_ratio     | AI 生成脚本代码占比      | 测试脚本生成 | % | percent | computed | `ai_code_lines/total_code_lines*100` | — |
| ai_code_lines            | AI 生成代码入库行数      | 测试脚本生成 | 行 | int     | sum      | — | ✅ |
| total_code_lines         | 入库代码总行数 (辅助列)  | 测试脚本生成 | 行 | int     | sum      | — | ✅ |
| ai_code_accuracy         | AI 生成代码准确率        | 测试脚本生成 | % | percent | weighted_avg | weight=`ai_code_lines` | — |
| new_script_ai_ratio      | 新增测试脚本 AI 辅助占比 | 测试脚本生成 | % | percent | weighted_avg | weight=`new_script_count` | — |
| new_script_count         | 新增测试脚本个数 (辅助)  | 测试脚本生成 | 个 | int     | sum      | — | ✅ |

> `ai_script_code_ratio` 是 `ai_code_lines / total_code_lines` 的展示，但**事实记录里只存 ai_code_lines 和 total_code_lines**。汇总时按行加权后再除。

## 3. 维度

| code | label | 形态 | 注释 |
|------|------|------|------|
| project   | 项目 | filter: multi_select | V1 必须 |
| domain    | 领域 | row dimension | V1 必须，是表格的行 |
| iteration | 迭代 | filter: multi_select 或 row dimension | V1.1 可扩展 |
| org       | 组织 | filter or row dimension | V1.2 可扩展（4 层路径） |

### 扩展点（保留不实现）

- `dim_iteration` 表已建（包含 `project_code` 外键），但 V1 不使用。
- `dim_org` 已建（含 `parent_code`、`depth`、`path`），V1 不使用。
- `ai_metric.iteration_code` / `ai_metric.org_path` 字段已留，V1 写 NULL。

### 维度行树状

- V1 只 1 级（领域）
- V1.1 增加 row_tree=`project>domain`（项目下分多个领域）
- V1.2 增加 row_tree=`org_l1>org_l2>org_l3>org_l4>domain`

## 4. 表结构

```sql
-- 项目
CREATE TABLE dim_project (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  code VARCHAR(64) NOT NULL UNIQUE,
  name VARCHAR(255) NOT NULL,
  is_active BOOLEAN DEFAULT TRUE,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 领域
CREATE TABLE dim_domain (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  code VARCHAR(64) NOT NULL UNIQUE,
  name VARCHAR(255) NOT NULL,
  sort_order INT DEFAULT 0
);

-- 迭代 (V1.1 扩展)
CREATE TABLE dim_iteration (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  code VARCHAR(64) NOT NULL,
  name VARCHAR(255) NOT NULL,
  project_code VARCHAR(64) NOT NULL,
  start_date DATE, end_date DATE,
  UNIQUE KEY uq_proj_iter (project_code, code)
);

-- 组织 (V1.2 扩展)
CREATE TABLE dim_org (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  code VARCHAR(64) NOT NULL UNIQUE,
  name VARCHAR(255) NOT NULL,
  parent_code VARCHAR(64),
  depth INT NOT NULL,                -- 1..4
  path VARCHAR(512) NOT NULL         -- "集团/事业部/部门/小组"
);

-- 指标定义（数据驱动，加指标不发版）
CREATE TABLE metric_def (
  code VARCHAR(64) PRIMARY KEY,
  label VARCHAR(255) NOT NULL,
  category VARCHAR(64) NOT NULL,         -- '测试设计' / '测试脚本生成'
  unit VARCHAR(32),                       -- '个'/'行'/'%'
  data_type VARCHAR(16) NOT NULL,         -- 'int' / 'decimal' / 'percent'
  agg_method VARCHAR(32) NOT NULL,        -- 'sum' / 'avg' / 'weighted_avg' / 'computed'
  computed_formula VARCHAR(255),          -- 'ai_req_count/req_count*100' / etc
  weight_metric VARCHAR(64),              -- weighted_avg 用的权重指标 code
  drilldown_enabled BOOLEAN DEFAULT FALSE,
  is_default_visible BOOLEAN DEFAULT TRUE,
  sort_order INT DEFAULT 0
);

-- 事实表（长表，加指标不改表）
CREATE TABLE ai_metric (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  period_date DATE NOT NULL,
  project_code VARCHAR(64) NOT NULL,
  domain_code VARCHAR(64) NOT NULL,
  iteration_code VARCHAR(64),       -- NULL = V1
  org_path VARCHAR(512),            -- NULL = V1
  metric_code VARCHAR(64) NOT NULL,
  metric_value DECIMAL(18,4) NOT NULL,
  source VARCHAR(64) DEFAULT 'manual',
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_metric (period_date, project_code, domain_code,
                        iteration_code, metric_code, source),
  INDEX idx_period (period_date),
  INDEX idx_proj_domain (project_code, domain_code),
  INDEX idx_metric (metric_code)
);

-- 明细记录（下钻用）
CREATE TABLE ai_metric_detail (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  period_date DATE NOT NULL,
  project_code VARCHAR(64) NOT NULL,
  domain_code VARCHAR(64) NOT NULL,
  iteration_code VARCHAR(64),
  metric_code VARCHAR(64) NOT NULL,
  detail_type VARCHAR(64) NOT NULL,    -- 'requirement' / 'case' / 'code_change'
  ref_id VARCHAR(128) NOT NULL,         -- 外部系统 ID (Jira / TestRail / Gitlab MR 等)
  ref_label VARCHAR(255),                -- 摘要/标题
  ref_url VARCHAR(512),                  -- 跳回原系统的链接
  is_ai_generated BOOLEAN DEFAULT FALSE,
  is_adopted BOOLEAN,                    -- 是否采纳（仅对部分明细类型有效）
  payload JSON,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_drill (period_date, project_code, domain_code, metric_code)
);
```

**MySQL 5.7 兼容性注意**：
- `JSON` 类型从 5.7.8 开始支持，OK。
- 无 CTE、无窗口函数：查询里只用 GROUP BY + 子查询。
- `utf8mb4` charset / `utf8mb4_unicode_ci` collation。

**PostgreSQL**：`JSON` → `JSONB`，由 SQLAlchemy 自动选择。
**SQLite (dev)**：`JSON` → `TEXT`，足够开发。

## 5. 聚合与计算

### 5.1 汇总（每个领域一行）

伪 SQL：
```sql
SELECT domain_code,
       SUM(CASE WHEN metric_code='req_count'      THEN metric_value END) AS req_count,
       SUM(CASE WHEN metric_code='ai_req_count'   THEN metric_value END) AS ai_req_count,
       SUM(CASE WHEN metric_code='new_case_count' THEN metric_value END) AS new_case_count,
       SUM(CASE WHEN metric_code='ai_case_count'  THEN metric_value END) AS ai_case_count,
       SUM(CASE WHEN metric_code='ai_code_lines'  THEN metric_value END) AS ai_code_lines,
       SUM(CASE WHEN metric_code='total_code_lines' THEN metric_value END) AS total_code_lines,
       SUM(CASE WHEN metric_code='new_script_count' THEN metric_value END) AS new_script_count
FROM ai_metric
WHERE period_date BETWEEN :from AND :to
  AND project_code IN (:projects)
GROUP BY domain_code;
```

之后在应用层算 computed 指标：
```python
row['ai_req_coverage']    = safe_pct(row['ai_req_count'], row['req_count'])
row['ai_case_ratio']      = safe_pct(row['ai_case_count'], row['new_case_count'])
row['ai_script_code_ratio'] = safe_pct(row['ai_code_lines'], row['total_code_lines'])
```

`weighted_avg` 类（采纳率 / 准确率 / 新脚本 AI 占比）的事实记录里**已经存"加权聚合中的分子分母"**，例如 `ai_case_adoption_rate` 实际存的是该 (项目, 领域, 日期) 下 AI 用例总数 × 采纳率（= 已采纳的 AI 用例数）。汇总时：
```python
row['ai_case_adoption_rate'] = safe_pct(adopted_ai_cases, ai_case_count)
```

### 5.2 合计行

同 §5.1 SQL，去掉 `GROUP BY domain_code`，整张表算一次。computed 列对合计行**重新算**（不能直接 sum 平均）。

### 5.3 下钻

```
POST /api/reports/ai_metrics/drilldown
{
  "ref": "metric_drill",
  "row":  {"domain_code":"core"},
  "filter":{"project":["proj_alpha"], "business_date":{"from":"2026-04-15","to":"2026-05-13"}},
  "cell": {"column":"ai_case_count"}
}
```

→
```sql
SELECT * FROM ai_metric_detail
WHERE period_date BETWEEN :from AND :to
  AND project_code IN (:projects)
  AND domain_code = :domain
  AND metric_code = :metric
  AND (CASE WHEN :metric LIKE 'ai_%' THEN is_ai_generated = TRUE ELSE TRUE END)
ORDER BY period_date DESC
LIMIT :ps OFFSET :off;
```

## 6. 扩展性保留

| 未来需求 | 已留接口 |
|---------|---------|
| 加新指标（如"AI 评审建议采纳数"）| 往 `metric_def` 插一行 + 上报 `ai_metric` 即可，前端 `/config` 自动加列 |
| 加新一级表头分组 | `metric_def.category` 改个新值即可 |
| 加新维度过滤（如版本号）| 往 `report_config.filters` JSON 加一条；事实表已有 source 字段，可继续加扩展 |
| 维度变成"项目+迭代"两级 | API 改 `dimension_path_codes=['project','iteration']`，渲染层树状已支持 |
| 维度变成"4 层组织 + 领域"| 同上，`dim_org` 已就绪 |
| 历史版本 / 来源版本 | 复用主设计的 `report_fact_version` + `source_mark`（V1 暂不开） |
| 视图模板 | 复用主设计的 view_template；本表预设 3 个（默认 / 紧凑 / 看板带KPI） |

## 7. 数据上报方式

支持三种 ingest 模式：

1. **HTTP POST** —— 接入方调 `POST /api/metrics/ingest`，一次报多条
2. **批量 CSV 导入** —— 管理员上传 CSV (`POST /api/metrics/ingest_csv`)
3. **定时拉取** —— 由 generation service (后续) 调外部 Jira / Gitlab / SonarQube 接口

V1 实现 1 + 2；3 留接口位。
