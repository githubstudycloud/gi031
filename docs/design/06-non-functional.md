# 06 · Non-Functional

## 1. 性能

| 指标 | 目标 | 措施 |
|---|---|---|
| `/config` P95 | < 80 ms | Redis 缓存 + ETag |
| 汇总查询 P95 (50 行 / 1 维度过滤) | < 300 ms | fact 表组合索引；列裁剪 |
| 详情查询 P95 (100 行) | < 500 ms | 分页强制；服务端可拒绝 page_size > 500 |
| 下拉首屏 P95 | < 200 ms | dropdown 缓存（60s）+ 收藏作为独立加权 |
| 单报表生成 (3 来源 / 1w 行) | < 10 min | 来源并发 + adapter 内分页并发 |

加速点：
- 事实表预聚合：常用维度组合做物化视图（按 region/产品 月聚合）。
- 列裁剪：summary 接口支持 `columns=` 参数。
- 大文本字段（lineage 详情）懒加载。

## 2. 可靠性

- **Celery worker** 失败不丢任务：result_backend 启用 + ack_late + visibility_timeout 高于最长任务。
- **DB 连接**：用 PgBouncer（事务级），SQLAlchemy 池大小 = 工作线程 × 1.2。
- **幂等键**：snapshot (report_type, business_date, source_code, version_no)；fact upsert 按 PK。
- **降级**：上游 API 5xx → 重试 N 次后放弃该分支；fact 用上一日；告警。

## 3. 安全

- JWT 强制 RS256，公钥从 JWKS 拉，缓存 1h。
- 管理 API 走单独 scope claim；非管理员请求所有 `/admin/*` 直接 403。
- 字段定义里的 SQL/HTTP source_config **不允许业务用户编辑**，必须管理员；后端在写入前做 SQL 模板白名单（参数化、禁止 `;`）。
- 下拉接口的 `q` 参数走参数化绑定，禁拼字符串。
- 个人偏好接口 user_id 取自 JWT，**不允许** body/query 指定他人 user_id。
- 审计日志：所有 admin 写入记录到 `audit_log` 表，附 actor / before / after。

## 4. 监控与告警

- 应用层：OpenTelemetry → OTLP Collector → Tempo / Prometheus / Loki。
- 业务指标：
  - `report_view_total{report_type}` 用量统计
  - `dropdown_query_total{key}` 评估缓存收益
  - `column_pref_save_total` 评估个人定制使用率
- 报警：
  - 单 flow_run 失败 → 告警 P3
  - 同来源连续 3 天失败 → P1
  - merger fallback 占比 > 10% → P2

## 5. 测试策略

| 层 | 框架 | 用例 |
|---|---|---|
| 单元 | pytest | 纯函数（mergeColumns / buildQueryParams / version pick） |
| 集成 | pytest + testcontainers | 真 PG / Redis；config 装配；事实表读写 |
| 端到端 | playwright | 通过 chrome-devtools-mcp 的等效自动化；走 §05 §9 checklist |
| 合约 | schemathesis | 用 OpenAPI 自动 fuzz |
| 性能 | k6 | 高并发 /config + /summary + /dropdowns |

CI：
- pre-commit：ruff / mypy / pytest-fast
- PR：测试 + alembic upgrade head + schemathesis
- nightly：长跑性能 + 真上游来源（用预录制）

## 6. 部署

`deploy/docker-compose.yml`（开发用）；`deploy/k8s/*` 生产用（后续）。

环境：
- `dev`：本地 compose；mock 上游
- `staging`：测试环境；真实上游只读
- `prod`：上线

Secrets：用 Vault 或云厂商 KMS；adapter 在运行时取，不入 ENV。

## 7. 数据迁移

- 元数据表用 Alembic 管理。
- 事实表 / 快照表的列变更：通过 admin 工单流（详见 §03 §5），生成的迁移走同一 Alembic 流。
- 历史数据：旧 fact 列即便 `physical_present=false` 也物理保留 ≥ 90 天，再走第二轮"清理工单"。

## 8. 性能与成本预估（粗略）

按预计：30 个 report_type、平均每天 5 个来源、每来源 1w 行：

- 快照存储：每天 30 × 5 × 1w × ~1KB ≈ 1.5 GB，全年 ~550 GB。压缩 + 冷归档（90 天后）。
- 事实表：每天 30 × 1w ≈ 300k 行，全年 ~110m 行；按 (业务日期, 维度) 分区，分区裁剪。
- Redis：config + 下拉缓存 < 1 GB。

## 9. 路线图

| 版本 | 范围 |
|---|---|
| **0.1** | M1 + M2：元数据 + 查询服务 + Vue/React demo |
| **0.2** | M3 + M4：生成服务 + 多来源 + 版本 + 合并 |
| **0.3** | M5：个人偏好 + 列定制 + 收藏 |
| **0.4** | M6：部署 + 自评估 + 文档 |
| **0.5** | 导出 Excel + 邮件订阅 |
| **0.6** | 行级权限 + 数据脱敏 |
| **1.0** | 多租户 + 自助维度切换 |

## 10. 风险登记

| 风险 | 影响 | 缓解 |
|---|---|---|
| 上游 API 频繁变更字段 | 生成失败 | adapter 层 field_mapping + 单元测试 + 上游契约监控 |
| flow_def 误配置 | 数据写错 | DAG 校验：节点引用字段必须在 field_def；merge_rule 引用列必须存在 |
| 物理删除字段误操作 | 数据丢失 | 多重审批 + 物理保留期 + 离线备份 |
| 下拉 SQL 注入 | 数据泄漏 | 管理员配置审核 + 参数化白名单 + 集成测试 |
| 个人偏好爆炸 | DB 膨胀 | 单用户上限（如 50 个 report × 2 view × 平均 30 行）+ 老数据清理 |
