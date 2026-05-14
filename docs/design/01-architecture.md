# 01 · Architecture

## 1. 进程拓扑

两个独立可执行的 Python 服务，共享元数据 DB：

| 服务 | 进程 | 端口 | 主要职责 |
|---|---|---|---|
| **Query Service** | `uvicorn services.query.app.main:app` | 8001 | 同步响应前端的配置/数据/下拉/偏好读写 |
| **Generation Service** | `celery -A services.generation.app.celery_app worker` | — | 消费 broker 任务，执行 DAG，写入事实/快照表 |
| **Generation Beat** | `celery -A services.generation.app.celery_app beat` | — | 定时触发（每日 T+1） |
| **Admin (复用 Query Service)** | 同 8001 | — | 字段/报表/流程的 CRUD 路由（带管理员权限） |

分进程的原因：
- **失败隔离**：抓数任务长跑、易超时，挂掉不能影响前端响应。
- **资源画像不同**：查询是 IO 密集 + 高并发短请求；生成是 IO + CPU 混合长任务。
- **可独立扩缩容**：worker 数 vs. uvicorn 副本数解耦。

## 2. 技术栈

| 关注点 | 选型 | 备注 |
|---|---|---|
| Web 框架 | **FastAPI** + `uvicorn[standard]` | async 端到端，OpenAPI 自动产出 |
| ORM | **SQLAlchemy 2.x** (async) + **Alembic** | 与 Pydantic 解耦，repo 层封装 |
| 校验 / 序列化 | **Pydantic v2** | 与 FastAPI 天然集成 |
| 任务编排 | **Celery 5** + **Redis** broker/result | 后续可替换 RabbitMQ |
| DAG / 并发 | Celery `group` + `chord` | 一个分支一个子任务；汇总用 chord 回调 |
| 元数据 DB | **PostgreSQL 15+** | 用 `JSONB` 装下拉/列配置的可扩展字段 |
| 缓存 | **Redis 7** | 下拉框、配置、用户偏好 |
| 鉴权 | **JWT (RS256)** | 由现有 SSO 颁发；服务只校验 |
| 日志 | `structlog` + JSON | 所有请求带 trace_id |
| 监控 | OpenTelemetry → OTLP | 后续接 Grafana/Tempo |

## 3. 模块切分

### 3.1 Query Service (`services/query`)

```
app/
├── main.py               # FastAPI 实例 + 中间件 + 路由注册
├── api/
│   ├── reports.py        # /api/reports/{type}/{config,summary,detail}
│   ├── dropdowns.py      # /api/dropdowns/{key}
│   ├── users.py          # /api/users/me/{favorites,columns}
│   └── admin/            # 管理后台 (字段 / 报表 / 流程定义)
├── domain/               # 纯业务逻辑，不依赖 FastAPI
│   ├── config_assembler.py   # 把多张元数据表组装成一份 "config" 响应
│   ├── dropdown_service.py
│   └── user_pref_service.py
├── repo/                 # 仓储层 (SQLAlchemy session 边界)
└── schemas/              # Pydantic 模型 (请求 / 响应)
```

### 3.2 Generation Service (`services/generation`)

```
app/
├── celery_app.py
├── tasks/
│   ├── orchestrator.py    # entry: run_report(report_type, business_date)
│   ├── branch.py          # 单分支执行
│   └── merger.py          # chord 回调：合并所有分支
├── flow/
│   ├── parser.py          # 把 flow_def JSON 解析成 DAG
│   └── runner.py          # 拓扑排序 + 并发分组
├── sources/
│   ├── base.py            # SourceAdapter ABC
│   ├── http_api.py
│   ├── sql.py
│   ├── file.py
│   └── registry.py        # 按 source_type 动态选择
├── merger/
│   ├── version_resolver.py  # 选择 valid 版本中最新的
│   └── column_merger.py     # 列级合并：哪些列来自哪个来源
└── snapshot.py            # 写快照 + source_mark
```

### 3.3 Shared (`libs/common`)

- SQLAlchemy 模型（事实表、快照表、源标记、配置表）
- 数据契约（`ReportConfig`、`FilterSpec`、`ColumnSpec` 等 Pydantic 模型）—— **同时被前端 TS 类型导出（用 datamodel-code-generator 或手写）**
- 错误码、trace_id 注入

## 4. 数据流（一次完整闭环）

```
[运营] 在 Admin 配置 report_type = "daily_sales"
   │
   ├─ field_def: 字段 + 默认列状态
   ├─ dropdown_def: filter 用到的下拉
   ├─ report_def: 标签页 / API 参数指引
   └─ flow_def: 抓取 DAG + 合并规则

[Celery Beat] 每天 02:00 触发
   │
   └─ generation.tasks.orchestrator.run_report("daily_sales", "2026-05-13")
        │
        ├─ parser 解析 flow_def → DAG
        ├─ group(branch_task_a, branch_task_b, branch_task_c).apply_async()
        │     ├─ branch_a 拉 source_X → 写 report_snapshot_<T> (version_id_1)
        │     ├─ branch_b 拉 source_Y → 写 report_snapshot_<T> (version_id_2)
        │     └─ branch_c 拉 source_Z → 写 report_snapshot_<T> (version_id_3)
        └─ chord 回调 → merger
              ├─ 读 source_mark 选 valid 版本
              ├─ 按列规则合并
              └─ upsert 到 report_fact_<T>

[前端] 用户访问 /reports/daily_sales
   │
   ├─ GET /api/reports/daily_sales/config
   │     → 返回 filter spec + column spec + tab spec
   │
   ├─ 根据 config 渲染筛选区、列选择器、tab 切换器
   │
   ├─ 对每个 filter 按 config.source.endpoint 调 /api/dropdowns/{key}
   │     (可带 parent / q / page / sort / 收藏标识)
   │
   └─ 用户点击查询 → 调 config.tabs[0].endpoint (= summary) / tabs[1].endpoint (= detail)
        → Query Service 走 report_fact_<T> 出结果
```

## 5. 关键依赖与版本基线

```
python = ">=3.11,<3.13"
fastapi = "^0.115"
uvicorn = "^0.30"
sqlalchemy = "^2.0"
alembic = "^1.13"
asyncpg = "^0.29"
pydantic = "^2.7"
celery = "^5.4"
redis = "^5.0"
structlog = "^24.1"
opentelemetry-* = "^1.27"
```

前端 demo:
```
vue: ^3.5      ·  @tanstack/vue-query: ^5
react: ^18.3   ·  @tanstack/react-query: ^5
axios: ^1.7
```

## 6. 部署形态

`deploy/docker-compose.yml` 起 7 个容器：

```
postgres      :5432       # 元数据 + 事实表
redis         :6379       # broker + 缓存
query-api     :8001       # uvicorn
celery-worker             # generation
celery-beat               # 调度
prometheus    :9090       # 可选
grafana       :3000       # 可选
```

生产用 k8s 时：
- query-api: Deployment + HPA (CPU + RPS)
- celery-worker: Deployment + 队列长度驱动的 KEDA HPA
- celery-beat: 单副本 StatefulSet (避免重复触发)
- DB / Redis: 托管服务

## 7. 配置变更生效路径

- 元数据表 update → Redis pub `config:invalidate:<report_type>` → Query Service 订阅清缓存 → 下一个请求重新组装。
- 版本号 (`report_def.version`) 单调递增，前端缓存 config 时按 version 失效。
