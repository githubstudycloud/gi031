# 部署 · AI 测试度量看板

目标主机：`192.168.0.132` (Ubuntu, OpenSSH 9.6)
账号：`ubuntu`
> 部署日志（实测）见本文末尾 §5 "实际部署记录"。

提供两条路径：
- **A. Docker Compose**（推荐 · 自带 MySQL 8.0 容器 · 一键起）
- **B. 原生 systemd**（无 Docker · 数据库自选）

## A. Docker Compose 部署 (推荐 · 一键脚本)

> v3：所有镜像源 / pip / npm / APT 都可通过 `.env` 配置自定义，**内网部署友好**。

### A.0 一键命令（公网）

```bash
cd apps/ai-metrics
cp deploy/.env.example deploy/.env       # 默认 = 公网官方源 + MySQL 8.0
bash deploy/deploy.sh up                 # build + up + seed + simulate
# 完成后访问 http://<host>:8001/ui/
```

### A.0' 一键命令（内网，所有源走私有仓）

```bash
cp deploy/.env.intranet.example deploy/.env
# 编辑 deploy/.env 把 nexus.intra / harbor.intra 改成你公司实际地址
bash deploy/deploy.sh up
```

`deploy/.env` 关键变量：

| 变量 | 作用 | 默认（公网） | 内网示例 |
|---|---|---|---|
| `PYTHON_IMAGE` | 基础 Python 镜像 | `python:3.12-slim` | `harbor.intra/library/python:3.12-slim` |
| `MYSQL_IMAGE`  | DB 镜像 | `mysql:8.0` | `harbor.intra/library/mysql:8.0` 或 `mysql:5.7` |
| `APT_MIRROR`   | Debian APT 镜像 | — | `http://nexus.intra/repository/debian-proxy` |
| `PIP_INDEX_URL`| pip 镜像 | — | `https://nexus.intra/repository/pypi-public/simple/` |
| `PIP_TRUSTED_HOST` | pip 自签证书 host | — | `nexus.intra` |
| `NPM_REGISTRY` | npm 镜像（占位） | — | `https://nexus.intra/repository/npm-public/` |
| `APP_PORT` / `MYSQL_PORT` | 对外端口 | 8001 / 3307 | 同 |
| `MYSQL_ROOT_PASSWORD` / `MYSQL_PASSWORD` | DB 密码 | rootpwd / aimpwd | **必改** |

公网常见镜像源（粘到 `.env`）：

```
# Tsinghua
APT_MIRROR=http://mirrors.tuna.tsinghua.edu.cn/debian
PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple/
NPM_REGISTRY=https://registry.npmmirror.com/
# Aliyun
APT_MIRROR=http://mirrors.aliyun.com/debian
PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
```

### A.1 deploy.sh 命令清单

```bash
bash deploy/deploy.sh init        # 生成 .env（若不存在；从 .env.example 复制）
bash deploy/deploy.sh build       # 仅 build 镜像
bash deploy/deploy.sh up          # build + up + seed + simulate
bash deploy/deploy.sh down        # 停容器（保留 volume）
bash deploy/deploy.sh nuke        # 停 + 删 + 删 volume（数据全清）
bash deploy/deploy.sh logs        # 跟随日志
bash deploy/deploy.sh ps          # 容器状态
bash deploy/deploy.sh seed [N]    # 在 api 容器内重灌 N 天假数据（默认 30）
bash deploy/deploy.sh simulate [src] [N]   # 跑模拟数据源（src=all/jira/.., N=天数）
bash deploy/deploy.sh shell       # 进 api 容器 bash
bash deploy/deploy.sh dbshell     # 进 mysql 容器 mysql client
```

### A.2 切 MySQL 版本（5.7 / 8.0 双向兼容）

```bash
# 用 5.7 验证兼容性
sed -i 's|MYSQL_IMAGE=.*|MYSQL_IMAGE=mysql:5.7|' deploy/.env
bash deploy/deploy.sh nuke && bash deploy/deploy.sh up
```

代码本身在 SQL 层避开 8.0 独占语法（无 CTE / 窗口函数 / generated column / check constraint / `utf8mb4_0900_*`），所有表统一 `utf8mb4 + utf8mb4_unicode_ci`。

### A.3 前置（系统级 docker，公网/内网通用）

宿主机有 `docker` + `docker compose v2`（Ubuntu 24.04 默认仓库就有）：

```bash
sudo apt-get update
sudo apt-get install -y docker.io docker-compose-v2 git
sudo usermod -aG docker $USER && newgrp docker
```

### A.2 拷贝代码

```bash
# 方法 1：git clone
git clone <repo-url> ~/gi031
cd ~/gi031/apps/ai-metrics

# 方法 2：scp 推
# (在 dev 机) scp -r apps/ai-metrics ubuntu@192.168.0.132:~/
```

### A.3 启动

```bash
cd ~/ai-metrics
docker compose -f deploy/docker-compose.yml up -d --build

# 等 db 健康
docker compose -f deploy/docker-compose.yml ps

# 初次需建表 + 灌假数据（容器内执行）
docker compose -f deploy/docker-compose.yml exec api \
  python -m app.seed.fake_data --days 30 --reset
```

### A.4 验证

```bash
curl -s http://localhost:8001/api/healthz
curl -s http://localhost:8001/api/reports/ai_metrics/config | head -c 400
# 浏览器打开
open http://192.168.0.132:8001/ui/      # 或在 dev 机访问
```

### A.5 切换 MySQL 版本

`docker-compose.yml` 改 `image: mysql:8.0` → `mysql:5.7`，重新 `up -d --build`。
（pymysql 与字符集兼容；不依赖 8.0 独占特性。）

---

## B. 原生 systemd 部署 (无 Docker)

### B.1 选择数据库

| DB | 配置 `DATABASE_URL` |
|---|---|
| SQLite （单机演示）| `sqlite:////opt/ai-metrics/ai_metrics.db` |
| MySQL 8.0 / 5.7    | `mysql+pymysql://ai_metrics:pwd@127.0.0.1:3306/ai_metrics?charset=utf8mb4` |
| PostgreSQL 14+     | `postgresql+psycopg://ai_metrics:pwd@127.0.0.1:5432/ai_metrics` |

### B.2 准备 DB （以 MySQL 8.0 为例）

```bash
sudo apt-get install -y mysql-server
sudo mysql <<SQL
CREATE DATABASE ai_metrics CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'ai_metrics'@'%' IDENTIFIED BY 'aimpwd';
GRANT ALL ON ai_metrics.* TO 'ai_metrics'@'%';
FLUSH PRIVILEGES;
SQL
```

### B.3 跑一键脚本

```bash
# 在 dev 机 (本仓库根目录)：
scp -r apps/ai-metrics ubuntu@192.168.0.132:~/
ssh ubuntu@192.168.0.132 'sudo bash ~/ai-metrics/deploy/install_native.sh "mysql+pymysql://ai_metrics:aimpwd@127.0.0.1:3306/ai_metrics?charset=utf8mb4"'
```

脚本会：
1. 安装 Python 3.12 + venv
2. 把代码拷到 `/opt/ai-metrics`
3. 装依赖（按 `DATABASE_URL` 自动选 `[mysql]` / `[postgres]`）
4. 写 `.env`（含 DATABASE_URL）
5. `python -m app.seed.fake_data --days 30 --reset`
6. 注册 + 启动 `systemctl enable --now ai-metrics`

### B.4 验证

```bash
ssh ubuntu@192.168.0.132 'systemctl status ai-metrics'
curl -s http://192.168.0.132:8001/api/healthz
```

### B.5 反向代理 (可选)

如要挂在 `http://192.168.0.132/ai-metrics/`：

```bash
sudo apt-get install -y nginx
sudo cp deploy/nginx.conf /etc/nginx/sites-available/ai-metrics
sudo ln -s /etc/nginx/sites-available/ai-metrics /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

---

## C. 数据库切换实操

服务跑起来后想换 DB（如从 SQLite 切到 MySQL）：

```bash
# 1) 改 /opt/ai-metrics/.env 里的 DATABASE_URL
sudo systemctl edit --full ai-metrics    # 或直接改 .env
# 2) 重启 + 重灌（注意：换 DB 等于换库，原数据不迁移）
sudo systemctl restart ai-metrics
sudo -u ubuntu /opt/ai-metrics/.venv/bin/python -m app.seed.fake_data --days 30 --reset
```

**MySQL 5.7 vs 8.0 注意**：本服务**只用 5.7 也支持的子集**——
- 表里没用 generated column / functional index
- 查询里没用 CTE / 窗口函数
- JSON 字段从 5.7.8 起原生支持，OK
- `utf8mb4` 必须，否则中文报错

---

## D. 后端日志 / 排查

```bash
sudo journalctl -u ai-metrics -f          # 实时日志
sudo journalctl -u ai-metrics -n 200 --no-pager
sudo systemctl status ai-metrics
```

常见错误：
- `Can't connect to MySQL server` → 检查 `.env` 的 DATABASE_URL 或 MySQL 端口/用户
- `NOT NULL constraint failed: dim_domain.id` → 用了 SQLite 但没用 Integer PK，本仓库已加 `with_variant`
- 端口 8001 占用 → `sudo ss -tlnp | grep 8001`

---

## E1. 查询独立模块部署（只读副本）

`app.main_query` 是只挂查询路由的入口（**不挂** `/api/metrics/ingest*`），可以独立部署做"只读副本"：

```bash
# 在 docker 上同一镜像跑两个 service：
# 1) 主服务（有 ingest）
docker compose -f deploy/docker-compose.yml up -d
# 2) 只读副本（共用同一份 DB）
docker run -d --name ai-metrics-query \
  --network <compose-network> \
  -e DATABASE_URL="mysql+pymysql://ai_metrics:pwd@db:3306/ai_metrics?charset=utf8mb4" \
  -p 8002:8001 \
  ai-metrics:local \
  uvicorn app.main_query:app --host 0.0.0.0 --port 8001 --workers 4
```

或更简：在容器内只用 `app.main_query` 启动：把 `docker-compose.yml` 的 `command:` 改为 `["uvicorn","app.main_query:app","--host","0.0.0.0","--port","8001","--workers","2"]`。

适用场景：
- 灰度发版（先把查询副本切到新版本验证）
- 防止 ingest 端点被误调用改坏数据
- 横向扩展只读副本

## E2. 接入到现有报表平台（gi031）

本服务遵循 [docs/design/03-api-spec](../../../docs/design/03-api-spec.md) 协议；任何兼容前端（gi031 主 prototype、Vue/React demo）都可以指向本服务的 `/api`：

```js
// 前端通过环境变量切换 API 地址
const API_BASE = import.meta.env.VITE_API_BASE || "http://192.168.0.132:8001";
```

---

## §5 实际部署记录 (2026-05-15)

> 执行人：Claude Code · 自动化 (paramiko 5.0.0)
> 目标：`192.168.0.132` / `ubuntu` / Ubuntu 24.04 LTS / Python 3.12.3
> 模式：**native + SQLite** （最快路径，无需 DB 服务）

### 环境探测（pre-flight）

```text
$ hostname && uname -a && lsb_release -a && python3 --version
ubuntu
Linux ubuntu 6.8.0-111-generic #111-Ubuntu SMP PREEMPT_DYNAMIC Sat Apr 11 23:16:02 UTC 2026 x86_64
Ubuntu 24.04.4 LTS  (Codename: noble)
Python 3.12.3

$ which docker mysql
/usr/bin/docker
mysql: not installed

$ df -h /
/dev/mapper/ubuntu--vg-ubuntu--lv  391G  31G  341G  9%
```

### 步骤 (paramiko 远程执行)

1. **打包** `apps/ai-metrics` → `/tmp/ai-metrics.tar.gz` (36 KB，排除 `.venv` / `*.db` / 截图)
2. **SCP 上传** 至 `192.168.0.132:/tmp/`
3. **解压** 到 `/home/ubuntu/ai-metrics/`
4. **`sudo apt-get install -y python3.12-venv`**（Ubuntu 24.04 默认不带）
5. **`python3 -m venv .venv`** + `pip install --upgrade pip`
6. **`pip install -e .`** (FastAPI + SQLAlchemy + Pydantic + uvicorn)
7. **写 `.env`**：`DATABASE_URL=sqlite:////home/ubuntu/ai-metrics/ai_metrics.db`
8. **`python -m app.seed.fake_data --days 14 --reset`** → `seeded 14 days × 4 projects × 6 domains`
9. **`nohup uvicorn app.main:app --host 0.0.0.0 --port 8001`** → PID `1857729`
10. **验证**：
    - `ss -tlnp | grep 8001` → `LISTEN 0  2048  0.0.0.0:8001`
    - `curl 127.0.0.1:8001/api/healthz` → `{"status":"ok"}`
    - `curl /api/reports/ai_metrics/config` → 完整 config JSON

### 验收

- 浏览器访问 [http://192.168.0.132:8001/ui/](http://192.168.0.132:8001/ui/) 渲染成功
- 6 个领域行 + 合计行（吸底）
- 时间段 / 项目多选过滤可用
- 截图保存在 `docs/_deployed_screenshot.png`（远程访问效果与本地一致）

### 运维命令

```bash
# 看日志
ssh ubuntu@192.168.0.132 'tail -100 /home/ubuntu/ai-metrics/uvicorn.log'

# 重启
ssh ubuntu@192.168.0.132 'pkill -f "app.main:app" && cd /home/ubuntu/ai-metrics && nohup .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8001 > uvicorn.log 2>&1 &'

# 升级（重灌假数据）
SSH_PASSWORD=*** python deploy/remote_deploy.py --host 192.168.0.132 --user ubuntu --mode native --db sqlite

# 拉个新指标
curl -X POST http://192.168.0.132:8001/api/metrics/ingest -H "Content-Type: application/json" -d '{"items":[{"period_date":"2026-05-15","project_code":"proj_alpha","domain_code":"core","metric_code":"req_count","metric_value":99,"source":"hand"}]}'
```

### 切到 systemd / Docker / MySQL（后续）

当前 deployment 用 `nohup` 起的，进程 reboot 后会消失。生产环境改为：
- **systemd**：执行 `sudo bash deploy/install_native.sh "$DATABASE_URL"`（脚本已就绪 → §B.3）
- **Docker Compose + MySQL 8.0**：执行 §A.3（远端已装 docker）；改 `DATABASE_URL` 一行即可。

---

## §6 v2 部署 (systemd 注册 + view_templates + 模拟来源 + Vue/React)

> 同样通过 paramiko 一锤子部署。本次新增内容：
> - `view_template` 表 + `/api/view_templates/{report_type}` 端点 + 2 个 backend 模板
> - `app/sources/simulate.py` 模拟 4 个数据来源（Jira / TestRail / Gitlab / Sonar）入 `ai_metric`
> - `frontend/vue/` Vue 3 demo + `frontend/react/` React 18 demo（CDN，无构建）
> - **systemd 单元 `ai-metrics.service`** 取代 nohup，开机自启 + 失败重启

### 步骤摘要

1. 打包代码（46 KB）+ SCP 上传
2. 停掉旧 `nohup` 进程
3. 解压覆盖（保留 `.venv` / `.env` / `*.db`）
4. `pip install -e .` 刷新依赖（新增 `app.sources` 包）
5. **重新 seed**（含 view_template 表）+ **跑一遍 simulate**（4 source × 2 day）
6. `sudo cp /tmp/ai-metrics.service /etc/systemd/system/` + `systemctl daemon-reload` + `enable --now`

### 验收输出

```
== install systemd unit (sudo)
Created symlink /etc/systemd/system/multi-user.target.wants/ai-metrics.service → /etc/systemd/system/ai-metrics.service.
active
● ai-metrics.service - AI Metrics Dashboard (FastAPI)
     Loaded: loaded (/etc/systemd/system/ai-metrics.service; enabled; preset: enabled)
     Active: active (running) since Fri 2026-05-15 12:53:13 CST; 2s ago
   Main PID: 1870220 (uvicorn)
      Tasks: 8 (limit: 19093)
     Memory: 154.2M
     CGroup: /system.slice/ai-metrics.service
             ├─1870220 .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8001 --workers 2

ss -tlnp | grep 8001
LISTEN 0  2048  0.0.0.0:8001  0.0.0.0:*  users:(("python3",…),("uvicorn",pid=1870220,fd=3))

GET / →
{"name":"ai-metrics","ui_html":"/ui/","ui_vue":"/vue/","ui_react":"/react/","api":"/api","docs":"/docs"}

GET /api/view_templates/ai_metrics →
{"code":0,"data":{"items":[{"code":"weekly_finance","name":"周度财务汇报","scope":"global","source":"backend","config":{...}}, ...]}}

GET /vue/   → HTTP/1.1 200 OK
GET /react/ → HTTP/1.1 200 OK
```

### 访问入口

| 形态 | URL |
|------|-----|
| 原生 HTML | http://192.168.0.132:8001/ui/ |
| Vue 3 demo | http://192.168.0.132:8001/vue/ |
| React 18 demo | http://192.168.0.132:8001/react/ |
| OpenAPI / Swagger | http://192.168.0.132:8001/docs |
| 健康检查 | http://192.168.0.132:8001/api/healthz |

### 运维

```bash
# 状态
systemctl status ai-metrics
journalctl -u ai-metrics -f          # 实时日志
journalctl -u ai-metrics -n 200 --no-pager

# 重启 / 停 / 启
sudo systemctl restart ai-metrics
sudo systemctl stop ai-metrics
sudo systemctl start ai-metrics

# 改完代码后升级（dev 机）
SSH_PASSWORD=*** python <<<'... paramiko script (见 git log 中的 v2 部署脚本)'

# 拉新指标 (手动 ingest 测试)
curl -X POST http://192.168.0.132:8001/api/metrics/ingest \
  -H "Content-Type: application/json" \
  -d '{"items":[{"period_date":"2026-05-15","project_code":"proj_alpha","domain_code":"core","metric_code":"req_count","metric_value":99,"source":"hand"}]}'

# 模拟来源（在远端跑，等同于"运行一次 cron"）
ssh ubuntu@192.168.0.132 'cd /home/ubuntu/ai-metrics && .venv/bin/python -m app.sources.simulate --source all --days 1'
```

### 让模拟来源定时跑（演示用，可选）

```bash
# 装 crontab 行（每 1 小时跑一次 simulate 当天数据）
ssh ubuntu@192.168.0.132 '(crontab -l 2>/dev/null; echo "5 * * * * cd /home/ubuntu/ai-metrics && .venv/bin/python -m app.sources.simulate --source all --days 1 >> /home/ubuntu/ai-metrics/simulate.log 2>&1") | crontab -'
```
