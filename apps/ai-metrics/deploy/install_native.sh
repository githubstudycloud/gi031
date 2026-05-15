#!/usr/bin/env bash
# 在 Ubuntu 22.04 / 24.04 上原生部署 ai-metrics（不依赖 Docker）。
# 用法：
#   sudo bash deploy/install_native.sh   <DATABASE_URL>
# 例：
#   sudo bash deploy/install_native.sh "sqlite:////opt/ai-metrics/ai_metrics.db"
#   sudo bash deploy/install_native.sh "mysql+pymysql://ai_metrics:pwd@127.0.0.1:3306/ai_metrics?charset=utf8mb4"
set -euo pipefail

DATABASE_URL="${1:-sqlite:////opt/ai-metrics/ai_metrics.db}"
APP_DIR="/opt/ai-metrics"
SERVICE_USER="ubuntu"

echo "==> 1) 安装系统包"
apt-get update -y
apt-get install -y python3.12 python3.12-venv python3-pip git

echo "==> 2) 拷贝代码到 $APP_DIR"
mkdir -p "$APP_DIR"
SRC_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cp -r "$SRC_DIR"/app  "$APP_DIR"/
cp -r "$SRC_DIR"/prototype "$APP_DIR"/
cp    "$SRC_DIR"/pyproject.toml "$APP_DIR"/
chown -R "$SERVICE_USER:$SERVICE_USER" "$APP_DIR"

echo "==> 3) 建 venv 装依赖"
sudo -u "$SERVICE_USER" python3.12 -m venv "$APP_DIR/.venv"
if [[ "$DATABASE_URL" == mysql* ]]; then
  EXTRAS="[mysql]"
elif [[ "$DATABASE_URL" == postgresql* ]]; then
  EXTRAS="[postgres]"
else
  EXTRAS=""
fi
sudo -u "$SERVICE_USER" "$APP_DIR/.venv/bin/pip" install --upgrade pip
sudo -u "$SERVICE_USER" "$APP_DIR/.venv/bin/pip" install -e "$APP_DIR$EXTRAS" || \
  sudo -u "$SERVICE_USER" "$APP_DIR/.venv/bin/pip" install -e "$APP_DIR"

echo "==> 4) 写入 .env"
cat > "$APP_DIR/.env" <<EOF
DATABASE_URL=$DATABASE_URL
CORS_ORIGINS=*
EOF
chown "$SERVICE_USER:$SERVICE_USER" "$APP_DIR/.env"
chmod 600 "$APP_DIR/.env"

echo "==> 5) 初始化 DB + 灌假数据"
cd "$APP_DIR"
sudo -u "$SERVICE_USER" bash -c "set -a; source .env; set +a; .venv/bin/python -m app.seed.fake_data --days 30 --reset"

echo "==> 6) 注册 systemd 单元"
cp "$SRC_DIR/deploy/ai-metrics.service" /etc/systemd/system/ai-metrics.service
systemctl daemon-reload
systemctl enable ai-metrics.service
systemctl restart ai-metrics.service

echo "==> 7) 检查端口"
sleep 2
ss -tlnp | grep 8001 || true

echo
echo "✅ 完成。访问："
HOST_IP="$(hostname -I | awk '{print $1}')"
echo "  - 前端：http://$HOST_IP:8001/ui/"
echo "  - OpenAPI Swagger：http://$HOST_IP:8001/docs"
echo "  - 健康检查：http://$HOST_IP:8001/api/healthz"
