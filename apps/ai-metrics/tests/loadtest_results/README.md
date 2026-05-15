# 压测结果 (2026-05-15)

## 环境

| 项 | 值 |
|---|---|
| 远端 | 192.168.0.132 · Ubuntu 24.04 · 16 GiB RAM · Docker 29.4.3 |
| 部署 | `deploy/deploy.sh up` → docker compose: `ai-metrics-api` (python:3.12-slim + uvicorn 2 workers) + `ai-metrics-db` (mysql:8.0) |
| 数据规模 | `dim_project=4`, `dim_domain=6`, `ai_metric=7670 行` (30 天 seed + 8 天 simulate × 4 source) |
| 压测客户端 | `ab -k` 从远端 localhost 内部打（**避开 Windows→Linux 跨网 NAT 干扰**） |

> **注**：从 Windows 客户端打 (`python -m tests.loadtest --base http://192.168.0.132:8001 -c 20 -n 30`) 时所有请求都 30s timeout，server 端 docker logs 无请求记录 —— 中间链路的 conntrack/NAT 在突发短连接下丢包。`tests/loadtest.py` 脚本本身是好的，但跨网压测需要走稳定链路；本次以 server-local `ab` 为准。

## 结果汇总

| Bench | 接口 | n | 并发 | RPS | p50 | p95 | p99 | max | 失败 |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| #1 | `GET /api/healthz`         | 2000 | 50  | **1492** | 31 ms  | 54 ms  | 70 ms  | 80 ms  | 0 |
| #2 | `GET /api/.../config`       | 500  | 20  | **249**  | 77 ms  | 123 ms | 138 ms | 155 ms | 0 |
| #3 | `POST /api/.../summary`     | 500  | 20  | **133**  | 139 ms | 253 ms | 336 ms | 379 ms | 0 |
| #4 | `POST /api/.../summary`     | 500  | 50  | **145**  | 331 ms | 523 ms | 606 ms | 781 ms | 0 |
| #5 | `POST /api/.../distinct`    | 500  | 20  | **250**  | 77 ms  | 121 ms | 143 ms | 168 ms | 0 |
| #6 | `GET /api/healthz`         | 5000 | 100 | **1620** | 56 ms  | 93 ms  | 121 ms | 149 ms | 0 |
| #7 | `POST /api/.../summary`     | 1000 | 50  | **137**  | 351 ms | 560 ms | 624 ms | 741 ms | 0 |

容器资源（压测高峰）：
- `ai-metrics-api`  CPU `0.7%` / Mem `200 MiB`
- `ai-metrics-db`   CPU `0.9%` / Mem `500 MiB`

## 结论

- `healthz` (静态 200) **1500+ RPS** —— uvicorn 2 worker 接近上限，**主要瓶颈是 worker 数**。
- `config` 全量装配（JOIN metric_def + dim_domain + 装配 header_tree）**~250 RPS**，p95 < 130 ms。
- `summary` 重聚合查询（11 atomic metrics × group by domain × derive computed）**~140 RPS**，p95 ~500 ms。
- `distinct` （domain_code group + count）**~250 RPS** —— 6 个值，缓存可观空间。
- **无失败请求**，CPU/Mem 都很闲（< 1%），瓶颈不在 DB，在 uvicorn worker 数。

## 提升空间

| 方向 | 预计提升 |
|---|---|
| `--workers 4` (从 2 → 4) | RPS ~2x，CPU 仍很闲 |
| `--workers 8` + DB pool size ↑ | summary RPS 应该到 ~500 |
| 给 fact 表加 `(period_date, project_code, domain_code, metric_code)` 联合索引 | summary p95 → 100ms 量级 |
| Redis 缓存 /config + /distinct (TTL 60s) | RPS 几乎无限 |
| ASGI HTTP/2 + 客户端连接复用 | 小请求 latency ↓ |
| MySQL 切到 PG / 上索引 | summary 重 query 提速 |

## 重跑命令

```bash
# 在 server 上跑：
ssh ubuntu@192.168.0.132 'docker exec ai-metrics-api curl -s http://localhost:8001/api/healthz'

# 从远端本机 ab：
ssh ubuntu@192.168.0.132 'echo "{\"paging\":\"none\",\"project_codes\":null}" > /tmp/body.json'
ssh ubuntu@192.168.0.132 'ab -k -n 1000 -c 50 -p /tmp/body.json -T application/json http://127.0.0.1:8001/api/reports/ai_metrics/summary'

# 从客户端跨网（注意 NAT 限制）：
python -m tests.loadtest --base http://192.168.0.132:8001 -c 10 -n 50 --timeout 60
```
