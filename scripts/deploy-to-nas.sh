#!/bin/bash
# NAS 一键部署脚本
# 用法: ./scripts/deploy-to-nas.sh [nas-host] [nas-user]

set -e

NAS_HOST=${1:-192.168.3.73}
NAS_USER=${2:-root}
PROJECT_DIR="/volume1/docker/ai-lab"

echo "🚀 部署 AI Lab 到 NAS ($NAS_HOST)..."

# 1. 确保目录存在
ssh $NAS_USER@$NAS_HOST "mkdir -p $PROJECT_DIR/{data,logs}"

# 2. 上传 .env 文件（如果不存在）
if ! ssh $NAS_USER@$NAS_HOST "test -f $PROJECT_DIR/.env"; then
    echo "📤 上传 .env 文件..."
    scp .env $NAS_USER@$NAS_HOST:$PROJECT_DIR/
fi

# 3. 上传 docker-compose 配置
echo "📤 上传 docker-compose.nas.yml..."
scp docker/docker-compose.nas.yml $NAS_USER@$NAS_HOST:$PROJECT_DIR/

# 4. 拉取最新镜像并启动
echo "🐳 拉取镜像并启动..."
ssh $NAS_USER@$NAS_HOST "cd $PROJECT_DIR && \
    docker pull ghcr.io/wincheshen/ai-closed-loop-lab/trading-agent:latest && \
    docker-compose -f docker-compose.nas.yml up -d webhook"

# 5. 检查状态
echo ""
echo "✅ 部署完成！检查服务状态："
sleep 2
curl -s http://$NAS_HOST:8002/health || echo "⚠️ Webhook 服务可能尚未启动"

echo ""
echo "📋 访问地址:"
echo "   - 交易记录管理: http://$NAS_HOST:8002/ui/"
echo "   - 策略选股:     http://$NAS_HOST:8002/ui/strategy.html"
echo "   - 健康检查:     http://$NAS_HOST:8002/health"
