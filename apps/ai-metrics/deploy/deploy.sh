#!/usr/bin/env bash
# Docker 一键部署 ai-metrics
#
# 子命令：
#   bash deploy/deploy.sh init        生成 .env (若不存在；从 .env.example 复制)
#   bash deploy/deploy.sh build       仅 build 镜像
#   bash deploy/deploy.sh up          build + start + seed
#   bash deploy/deploy.sh down        停止并删除容器（保留 volume）
#   bash deploy/deploy.sh nuke        停止 + 删容器 + 删 volume（数据全清）
#   bash deploy/deploy.sh logs        跟随日志
#   bash deploy/deploy.sh ps          容器状态
#   bash deploy/deploy.sh seed        在 api 容器内重灌假数据
#   bash deploy/deploy.sh simulate    在 api 容器内跑模拟数据源
#   bash deploy/deploy.sh shell       进 api 容器 bash
#   bash deploy/deploy.sh dbshell     进 mysql 容器 mysql client
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="$SCRIPT_DIR/.env"
COMPOSE="docker compose --env-file $ENV_FILE -f $SCRIPT_DIR/docker-compose.yml"

# Compose v2 vs v1 兼容（v1 是 docker-compose）
if ! command -v docker >/dev/null 2>&1; then echo "需要 docker"; exit 1; fi
if ! docker compose version >/dev/null 2>&1; then
    if command -v docker-compose >/dev/null 2>&1; then
        COMPOSE="docker-compose --env-file $ENV_FILE -f $SCRIPT_DIR/docker-compose.yml"
    else
        echo "需要 docker compose (v2) 或 docker-compose (v1)"; exit 1
    fi
fi

ensure_env() {
    if [ ! -f "$ENV_FILE" ]; then
        echo "[init] $ENV_FILE 不存在；从 .env.example 复制"
        cp "$SCRIPT_DIR/.env.example" "$ENV_FILE"
        echo "[init] 请按需编辑 $ENV_FILE 后再次运行"
    fi
}

case "${1:-up}" in
    init)
        ensure_env
        ;;
    build)
        ensure_env
        $COMPOSE build --pull
        ;;
    up)
        ensure_env
        echo "[1/3] 构建镜像"; $COMPOSE build --pull
        echo "[2/3] 启动 db + api"; $COMPOSE up -d
        echo "[3/3] 等待 db 健康，然后 seed + simulate"
        for i in $(seq 1 30); do
            status=$(docker inspect -f '{{.State.Health.Status}}' ai-metrics-db 2>/dev/null || echo "starting")
            [ "$status" = "healthy" ] && break
            echo "    db state=$status (${i}/30)"; sleep 3
        done
        # 容器内 seed（DATABASE_URL 已注入）
        $COMPOSE exec -T api python -m app.seed.fake_data --days 30 --reset
        $COMPOSE exec -T api python -m app.sources.simulate --source all --days 2 || true
        echo
        $COMPOSE ps
        echo
        APP_PORT=$(grep -E "^APP_PORT=" "$ENV_FILE" | cut -d= -f2 || echo 8001)
        echo "✅ 完成。访问："
        echo "   原生 HTML : http://<host>:${APP_PORT}/ui/"
        echo "   Vue       : http://<host>:${APP_PORT}/vue/"
        echo "   React     : http://<host>:${APP_PORT}/react/"
        echo "   API 文档  : http://<host>:${APP_PORT}/docs"
        ;;
    down)
        $COMPOSE down
        ;;
    nuke)
        $COMPOSE down -v
        ;;
    logs)
        $COMPOSE logs -f --tail=200
        ;;
    ps)
        $COMPOSE ps
        ;;
    seed)
        $COMPOSE exec api python -m app.seed.fake_data --days "${2:-30}" --reset
        ;;
    simulate)
        $COMPOSE exec api python -m app.sources.simulate --source "${2:-all}" --days "${3:-1}"
        ;;
    shell)
        $COMPOSE exec api bash
        ;;
    dbshell)
        $COMPOSE exec db mysql -uai_metrics -p"$(grep -E "^MYSQL_PASSWORD=" "$ENV_FILE" | cut -d= -f2)" ai_metrics
        ;;
    *)
        echo "用法: $0 {init|build|up|down|nuke|logs|ps|seed|simulate|shell|dbshell}"
        exit 2
        ;;
esac
