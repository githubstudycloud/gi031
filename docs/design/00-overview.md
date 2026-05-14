# 00 · Overview · Report Platform (gi031)

> 项目仓库: https://github.com/githubstudycloud/gi031.git
> 当前分支: `feat/report-platform-design`
> 起草日期: 2026-05-14

## 1. 问题陈述

业务上需要一个 **可配置、可扩展的报表平台**，覆盖两类核心场景：

1. **查询场景** —— 前端按"报表类型"动态拉取配置后渲染：汇总表 / 详情表 / 下拉框 / 列定义全部由后端配置驱动，前端代码对单一报表类型无侵入。
2. **生成场景** —— 后端按"报表类型 + 业务日期"运行流程化生成，**同一列字段允许多种来源**，每个来源独立保存历史版本，支持人工标记某来源某版本的"有效/无效"，最终按规则合并得到当日最终版。

两个服务共享一套元数据（报表定义、字段定义、流程定义），但运行进程独立，可独立扩缩容。

## 2. 目标与非目标

### 目标
- 新增 / 修改一类报表，**只改配置不改前端代码**。
- 字段定义支持"软隐藏 + 物理保留"，物理删除必须改生成代码后才允许（避免历史数据列丢失）。
- 下拉框统一抽象：**层级下拉 + 搜索下拉**，统一支持分页 / 自定义排序 / 个人收藏 / 筛选。
- 列展示有两套状态：**后端默认 → 个人定制**；个人定制可保存为"我的默认"。
- 报表生成流程支持 **DAG 形式的并发分支**，按列粒度选择来源，按版本与有效性合并。
- 提供 **Vue 3 + React 18** 两份对接示例，证明协议是前端框架无关的。
- 自评估接入 chrome-devtools-mcp，原型在浏览器跑通。

### 非目标 (本阶段不做)
- 报表导出（Excel/PDF）—— 后续迭代。
- 多租户权限隔离 —— 用 user_id 区分个人偏好即可，行级权限留位但不实现。
- 实时增量更新 —— 先做"按业务日期 T+1"批处理，不做秒级实时。
- 自助 BI 拖拽建模 —— 配置仍由开发/运营在管理后台维护，不开放业务自助拼接。

## 3. 顶层架构

```
┌───────────────────────────────────────────────────────────────────────┐
│                              Frontend                                 │
│            ┌─────────────────┐         ┌─────────────────┐            │
│            │  Vue 3 demo     │         │  React 18 demo  │            │
│            └────────┬────────┘         └────────┬────────┘            │
│                     │ 同一组 REST API           │                     │
└─────────────────────┼───────────────────────────┼─────────────────────┘
                      │                           │
            ┌─────────▼───────────────────────────▼──────────┐
            │      Query Service  (FastAPI, async)           │
            │  /api/reports/{type}/config | summary | detail │
            │  /api/dropdowns/{key}        favorites / prefs │
            └─────────┬──────────────────────────────┬───────┘
                      │ read                         │ read/write
                      │                              │
        ┌─────────────▼──────────┐         ┌─────────▼──────────┐
        │   Metadata DB          │         │   Redis (cache)    │
        │   PostgreSQL           │         │   dropdown cache,  │
        │   - field_def          │         │   config cache,    │
        │   - report_def         │         │   session state    │
        │   - dropdown_def       │         └────────────────────┘
        │   - user_*             │
        │   - flow_def / run     │                  ▲
        │   - report_fact_<T>    │                  │ broker
        │   - report_snapshot_<T>│         ┌────────┴───────────┐
        │   - source_mark        │         │  Celery broker     │
        └─────────────▲──────────┘         │  (Redis or RabbitMQ│
                      │ write                └────────┬─────────┘
                      │                              │
            ┌─────────┴──────────────────────────────▼─────────┐
            │     Generation Service (Celery workers)          │
            │  flow runner · branch dispatcher · merger        │
            └─────────┬────────────────────┬───────────────────┘
                      │ pull               │ pull
              ┌───────▼────────┐    ┌──────▼────────┐
              │ External APIs  │    │ External DBs  │
              │ (per source)   │    │ (per source)  │
              └────────────────┘    └───────────────┘
```

## 4. 仓库结构（计划）

```
gi031/
├── .mcp.json                         # chrome-devtools-mcp 注册
├── .gitignore
├── README.md
├── docs/
│   └── design/                       # ← 本目录，先于编码确认
│       ├── 00-overview.md
│       ├── 01-architecture.md
│       ├── 02-data-model.md
│       ├── 03-api-spec.md
│       ├── 04-flow-engine.md
│       ├── 05-frontend-integration.md
│       └── 06-non-functional.md
├── prototypes/
│   └── index.html                    # ← 静态 HTML 原型，浏览器直接打开
├── services/
│   ├── query/                        # FastAPI 查询服务
│   │   ├── pyproject.toml
│   │   ├── app/
│   │   │   ├── main.py
│   │   │   ├── api/
│   │   │   ├── domain/
│   │   │   ├── repo/
│   │   │   └── schemas/
│   │   └── tests/
│   └── generation/                   # Celery 生成服务
│       ├── pyproject.toml
│       ├── app/
│       │   ├── celery_app.py
│       │   ├── tasks/
│       │   ├── sources/              # 不同来源适配器
│       │   ├── merger/
│       │   └── flow/                 # DAG 解释器
│       └── tests/
├── libs/
│   └── common/                       # 共享 SQLAlchemy 模型、Pydantic、契约
└── deploy/
    ├── docker-compose.yml
    └── alembic/                      # 迁移脚本
```

## 5. 阶段性里程碑

| 阶段 | 范围 | 输出 |
|---|---|---|
| **M0 设计确认** (本阶段) | 6 份设计文档 + 1 份 HTML 原型 | 你确认后进入 M1 |
| **M1 元数据 + 查询服务骨架** | 表结构、Alembic、配置/下拉/汇总/详情 API | 可用 curl 跑通 |
| **M2 前端示例** | Vue + React 各一个示例工程 | 浏览器可见 |
| **M3 生成服务骨架** | flow_def / 一个简单 DAG 跑通 | 单报表能并发拉取 + 合并 |
| **M4 多来源 + 版本快照 + 有效性合并** | 完整生成路径 | E2E：配置 → 生成 → 查询 |
| **M5 个人偏好 + 列定制 + 收藏** | 用户级写入 + 缓存 | 完整体验 |
| **M6 部署 + 自评估** | docker-compose、chrome-devtools-mcp 自检脚本 | 可演示 |

## 6. 读者顺序建议

1. `00-overview.md` （本文）
2. `01-architecture.md` —— 进程边界与依赖
3. `02-data-model.md` —— 元数据 / 事实 / 版本 / 用户偏好的表结构
4. `03-api-spec.md` —— **核心：自描述配置协议**
5. `04-flow-engine.md` —— 生成流程 DAG / 来源 / 版本 / 合并
6. `05-frontend-integration.md` —— Vue + React 接入示例
7. `06-non-functional.md` —— 性能 / 安全 / 部署 / 测试
