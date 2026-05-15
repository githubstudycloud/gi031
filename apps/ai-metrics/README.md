# AI 测试度量看板 (ai-metrics)

报表平台的一个**具体业务实例**：度量"AI 在测试设计 / 测试脚本生成"环节的渗透程度。
遵循 `docs/design/` 中的报表平台协议（自描述 `/config`、`header_tree`、`row_filter`、drilldown、view template），后端是 FastAPI，前端是单文件 HTML 原型。

## 业务模板

二级表头，按 **领域** 一行展示：

```
| 领域 |    测试设计 (7 指标)                                    |  测试脚本生成 (4 指标)                                |
|------|---------------------------------------------------------|--------------------------------------------------------|
|      | 需求 | AI需求 | AI需求    | 新增 | AI用例 | 用例AI  | 用例AI |  AI脚本   | AI代码 | AI代码 | 新脚本AI |
|      | 个数 | 个数  | 覆盖率    | 用例 | 个数  | 生成占比 | 采纳率 |  代码占比 | 入库行 | 准确率 | 辅助占比 |
| 核心 |  ... | ...   | 67%      | ...  | ...   | 64%     | 82%   |  35%      | 12,340 | 91%   | 42%     |
| 业务 |  ... | ...   | ...      | ...  | ...   | ...     | ...   |  ...      | ...    | ...   | ...     |
| 合计 | sum  | sum   | weighted | sum  | sum   | weighted| avg   |  weighted | sum    | avg   | weighted|
```

- 维度：**项目** (multi_select)。未来可扩 **项目+迭代**、或 **4 层组织**。
- 表头列可由 `metric_def` 表配置动态增减；后端不发版即可加新指标，前端自动渲染。
- "个数 / 入库行" 类列点击可下钻到明细。

## 目录结构

```
apps/ai-metrics/
├── README.md                  ← 本文件
├── docs/
│   ├── design.md              业务模板设计 (字段口径、聚合规则、扩展点)
│   ├── api.md                 接口文档 + curl/httpie/Python 示例
│   └── deployment.md          Ubuntu 部署步骤（systemd / docker-compose）
├── prototype/
│   └── index.html             业务模板专用单文件原型 (可独立打开 + 接后端)
├── app/                       FastAPI 后端
│   ├── main.py
│   ├── settings.py            DATABASE_URL 等 env 配置
│   ├── db.py                  SQLAlchemy engine 解耦（sqlite/mysql 5.7/8.0/pg）
│   ├── models.py
│   ├── schemas.py
│   ├── api/
│   │   ├── config.py          GET /api/reports/ai_metrics/config
│   │   ├── data.py            POST /api/reports/ai_metrics/summary | distinct | drilldown
│   │   ├── ingest.py          POST /api/metrics/ingest (上报指标)
│   │   └── meta.py            GET /api/dropdowns/projects, /api/healthz
│   ├── services/
│   │   ├── config_assembler.py
│   │   ├── metric_query.py    SQL 构造 + 聚合 + computed
│   │   └── drilldown.py
│   └── seed/
│       └── fake_data.py       python -m app.seed.fake_data 一键灌假数据
├── alembic/
│   ├── env.py
│   └── versions/001_initial.py
├── alembic.ini
├── pyproject.toml
├── deploy/
│   ├── Dockerfile
│   ├── docker-compose.yml     FastAPI + MySQL 8.0 + 静态前端
│   ├── nginx.conf
│   ├── ai-metrics.service     systemd unit
│   └── install_native.sh      纯系统包安装脚本（备选）
└── tests/
    └── test_smoke.py
```

## 快速开始 (本地)

```bash
cd apps/ai-metrics
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -e .

# 默认 SQLite（不依赖 DB），开箱即用
alembic upgrade head
python -m app.seed.fake_data
uvicorn app.main:app --reload --port 8001

# 打开前端：浏览器访问 prototype/index.html，或服务化：
python -m http.server 8002 --directory prototype     # 然后访问 :8002
```

切到 MySQL 8.0 / 5.7 / PostgreSQL，只需改 env：

```bash
export DATABASE_URL="mysql+pymysql://user:pwd@host:3306/ai_metrics?charset=utf8mb4"
# 或
export DATABASE_URL="postgresql+psycopg://user:pwd@host:5432/ai_metrics"
```

详见 [docs/deployment.md](docs/deployment.md)。
