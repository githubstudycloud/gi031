# 部署 · AI 测试度量看板

目标主机：`192.168.0.132` (Ubuntu, OpenSSH 9.6)
账号：`ubuntu`
> 部署日志（实测）见本文末尾 §5 "实际部署记录"。

提供两条路径：
- **A. Docker Compose**（推荐 · 自带 MySQL 8.0 容器 · 一键起）
- **B. 原生 systemd**（无 Docker · 数据库自选）

## A. Docker Compose 部署 (推荐)

### A.1 前置

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

## E. 接入到现有报表平台（gi031）

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
