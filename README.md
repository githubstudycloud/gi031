# gi031 · Report Platform

可配置、可扩展的报表平台。后端 **FastAPI + Celery**，前端 **Vue 3 / React 18 双端示例**，元数据驱动渲染。

## 当前阶段

`feat/report-platform-design` —— **设计稿确认中**。先看 `docs/design/`，打开 `prototypes/index.html`，确认后进入编码。

## 文档地图

- [00-overview](docs/design/00-overview.md) · 问题、目标、里程碑
- [01-architecture](docs/design/01-architecture.md) · 进程拓扑、技术栈
- [02-data-model](docs/design/02-data-model.md) · 表结构、ER
- [03-api-spec](docs/design/03-api-spec.md) · **核心：自描述配置协议**
- [04-flow-engine](docs/design/04-flow-engine.md) · 生成 DAG / 多来源 / 版本 / 合并
- [05-frontend-integration](docs/design/05-frontend-integration.md) · Vue + React 示例
- [06-non-functional](docs/design/06-non-functional.md) · 性能 / 安全 / 部署 / 测试

## 原型

直接双击或拖到 Chrome 打开：[prototypes/index.html](prototypes/index.html)

```
prototypes/
└── index.html     # 单文件，零依赖，含 mock 配置 + mock 数据
```

## MCP

`.mcp.json` 已注册 `chrome-devtools-mcp`，由 Claude Code 启动时通过 npx 拉起，用于自评估浏览器原型。
