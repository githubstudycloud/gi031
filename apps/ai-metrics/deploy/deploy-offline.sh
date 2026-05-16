#!/usr/bin/env bash
# 离线 / 内网部署一键脚本
#
# 与 deploy.sh 区别：
#   - 不下载 mysql/nginx 镜像
#   - 内网 pip 源（必填）
#   - 连 host 上 mysql 5.7
#   - 前端 JS 全部从 frontend/vendor/ 本地加载
#
# 子命令：
#   init       生成 .env.offline 模板
#   setup-db   打印 mysql 建库 / 建用户 SQL
#   build      构建镜像（只用内网 pip 源）
#   up         build + 启动 api（不起 db）+ seed + simulate
#   down       停止
#   nuke       停止 + 删镜像
#   logs       日志
#   ps         容器状态
#   seed [N]   重灌 N 天假数据（默认 30）
#   simulate [src] [N]   跑模拟数据源
#   shell      进 api 容器
#   verify     curl 验证 7 个端点
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="$SCRIPT_DIR/.env.offline"
COMPOSE_FILE="$SCRIPT_DIR/docker-compose.offline.yml"
COMPOSE="docker compose --env-file $ENV_FILE -f $COMPOSE_FILE"

# v2 / v1 兼容
if ! docker compose version >/dev/null 2>&1; then
    if command -v docker-compose >/dev/null 2>&1; then
        COMPOSE="docker-compose --env-file $ENV_FILE -f $COMPOSE_FILE"
    else
        echo "需要 docker compose (v2) 或 docker-compose (v1)"; exit 1
    fi
fi

require_env() {
    if [ ! -f "$ENV_FILE" ]; then
        echo "❌ $ENV_FILE 不存在；先跑 $0 init 后再编辑"
        exit 1
    fi
    # V4: 检测 DATABASE_URL 仍是 CHANGE-ME 默认值，提前拦截
    if grep -E "^DATABASE_URL=" "$ENV_FILE" | grep -q "CHANGE-ME"; then
        echo "❌ $ENV_FILE 中的 DATABASE_URL 还是默认占位密码 (CHANGE-ME)；"
        echo "   请改成你 host 上 mysql 真实的连接串后重试。"
        exit 1
    fi
    # V4: 鼓励生产配 ADMIN_TOKEN（不强制；空就 warn）
    if ! grep -E "^ADMIN_TOKEN=.+" "$ENV_FILE" >/dev/null; then
        echo "⚠ ADMIN_TOKEN 未设置 — admin/ingest 端点将以 dev 模式开放（无鉴权）。"
        echo "  生产部署请在 $ENV_FILE 中设置 ADMIN_TOKEN=<长字符串>。"
    fi
}

print_setup_db() {
    cat <<'SQL'
-- 在 host 上的 MySQL 5.7 执行 (mysql -uroot -p)
CREATE DATABASE IF NOT EXISTS ai_metrics
    CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

CREATE USER IF NOT EXISTS 'ai_metrics'@'%'
    IDENTIFIED BY 'CHANGE-ME-PWD';

GRANT ALL PRIVILEGES ON ai_metrics.* TO 'ai_metrics'@'%';
FLUSH PRIVILEGES;
SQL
    echo
    echo "改完密码后写到 deploy/.env.offline 的 DATABASE_URL 中。"
    echo "如果 host mysql 仅绑 127.0.0.1，需要让它监听 0.0.0.0 (改 my.cnf 的 bind-address)"
    echo "或使用 docker host network: 把 docker-compose.offline.yml 的 ports 段换成 network_mode: host"
}

case "${1:-up}" in
    init)
        if [ -f "$ENV_FILE" ]; then echo "已存在：$ENV_FILE"; else
            cp "$SCRIPT_DIR/.env.offline.example" "$ENV_FILE"
            echo "[init] 生成 $ENV_FILE，编辑后再次运行"
        fi
        ;;
    setup-db)
        print_setup_db
        ;;
    build)
        require_env
        $COMPOSE build --pull=false
        ;;
    up)
        require_env
        echo "[1/4] 构建镜像 (走 .env.offline 里的内网 pip 源)"
        $COMPOSE build --pull=false
        echo "[2/4] 启动 api 容器"
        $COMPOSE up -d
        echo "[3/4] 等待 api 起来"
        APP_PORT=$(grep -E "^APP_PORT=" "$ENV_FILE" | cut -d= -f2 || echo 8001)
        for i in $(seq 1 30); do
            if curl -sf "http://localhost:${APP_PORT}/api/healthz" >/dev/null 2>&1; then
                echo "  ✓ api healthy"; break
            fi
            echo "  waiting api... ($i/30)"; sleep 2
        done
        echo "[4/4] seed + simulate"
        $COMPOSE exec -T api python -m app.seed.fake_data --days 30 --reset
        $COMPOSE exec -T api python -m app.sources.simulate --source all --days 2 || true
        echo
        $COMPOSE ps
        echo
        echo "✅ 完成。访问："
        echo "   原生 HTML : http://<host>:${APP_PORT}/ui/"
        echo "   Vue       : http://<host>:${APP_PORT}/vue/"
        echo "   React     : http://<host>:${APP_PORT}/react/"
        echo "   API 文档  : http://<host>:${APP_PORT}/docs"
        ;;
    down)   require_env; $COMPOSE down ;;
    nuke)   require_env; $COMPOSE down --rmi local ;;
    logs)   require_env; $COMPOSE logs -f --tail=200 ;;
    ps)     require_env; $COMPOSE ps ;;
    seed)
        require_env
        $COMPOSE exec api python -m app.seed.fake_data --days "${2:-30}" --reset
        ;;
    simulate)
        require_env
        $COMPOSE exec api python -m app.sources.simulate --source "${2:-all}" --days "${3:-1}"
        ;;
    shell)  require_env; $COMPOSE exec api bash ;;
    verify)
        require_env
        APP_PORT=$(grep -E "^APP_PORT=" "$ENV_FILE" | cut -d= -f2 || echo 8001)
        GEN_PORT=$(grep -E "^GEN_PORT=" "$ENV_FILE" | cut -d= -f2 || echo 8002)
        BASE="http://localhost:${APP_PORT}"
        GEN="http://localhost:${GEN_PORT}"
        echo "--- query /api/healthz"
        curl -sf "$BASE/api/healthz" || echo FAIL
        echo
        echo "--- generation /healthz + scheduled jobs"
        curl -sf "$GEN/" || echo FAIL
        echo
        echo "--- /api/dropdowns/projects"
        curl -sf "$BASE/api/dropdowns/projects" | head -c 200 || echo FAIL
        echo
        echo "--- /api/reports/ai_metrics/config"
        curl -sf "$BASE/api/reports/ai_metrics/config" | head -c 200 || echo FAIL
        echo
        echo "--- /api/reports/ai_metrics/summary"
        curl -sf -X POST "$BASE/api/reports/ai_metrics/summary" -H "Content-Type: application/json" -d '{"paging":"none"}' | head -c 200 || echo FAIL
        echo
        echo "--- /api/view_templates/ai_metrics"
        curl -sf "$BASE/api/view_templates/ai_metrics" | head -c 200 || echo FAIL
        echo
        echo "--- /vendor/vue.esm-browser.prod.js (本地 JS)"
        curl -sIo /dev/null -w "%{http_code}\n" "$BASE/vendor/vue.esm-browser.prod.js"
        echo "--- /vue/ /react/ /ui/ /admin/"
        curl -sIo /dev/null -w "ui:     %{http_code}\n" "$BASE/ui/"
        curl -sIo /dev/null -w "vue:    %{http_code}\n" "$BASE/vue/"
        curl -sIo /dev/null -w "react:  %{http_code}\n" "$BASE/react/"
        curl -sIo /dev/null -w "admin:  %{http_code}\n" "$BASE/admin/"
        ;;
    *)
        cat <<EOF
用法: $0 {init|setup-db|build|up|down|nuke|logs|ps|seed [N]|simulate [src] [N]|shell|verify}

典型流程:
  bash $0 init              # 第一次：生成 .env.offline
  vi deploy/.env.offline    # 改 PIP_INDEX_URL / DATABASE_URL / 密码
  bash $0 setup-db          # 看 mysql 建库 SQL，去 host mysql 执行
  bash $0 up                # 构建 + 启动 + seed + simulate
  bash $0 verify            # curl 验证 8 个端点
EOF
        exit 2
        ;;
esac
