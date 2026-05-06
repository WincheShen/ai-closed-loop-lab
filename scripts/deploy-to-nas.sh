#!/bin/bash
# NAS 一键部署脚本
# 用法: ./scripts/deploy-to-nas.sh [nas-host] [nas-user]
# 前提: NAS 上已 sudo docker login ghcr.io（参见 docs/nas-deployment.md）

set -e

NAS_HOST=${1:-192.168.3.73}
NAS_USER=${2:-kingsy_9}
PROJECT_DIR="/volume1/docker/ai-lab-web"

echo "🚀 部署 AI Lab Web 到 NAS ($NAS_USER@$NAS_HOST)..."
echo "   目标目录: $PROJECT_DIR"
echo ""

# 1. 确保目录存在
echo "📁 创建目录..."
ssh $NAS_USER@$NAS_HOST "sudo mkdir -p $PROJECT_DIR/{data,logs}"

# 2. 上传 docker-compose 配置
echo "📤 上传 docker-compose.nas.yml..."
scp docker/docker-compose.nas.yml $NAS_USER@$NAS_HOST:$PROJECT_DIR/

# 3. 上传 .env 文件（如果远程不存在）
ssh $NAS_USER@$NAS_HOST "test -f $PROJECT_DIR/.env" 2>/dev/null || {
    echo "📤 上传 .env 文件..."
    scp .env $NAS_USER@$NAS_HOST:$PROJECT_DIR/
}

# 4. 拉取最新镜像并启动
echo "🐳 拉取镜像并启动..."
ssh $NAS_USER@$NAS_HOST "cd $PROJECT_DIR && \
    sudo docker pull ghcr.io/wincheshen/ai-closed-loop-lab/trading-agent:latest && \
    sudo docker-compose -f docker-compose.nas.yml down 2>/dev/null; \
    sudo docker-compose -f docker-compose.nas.yml up -d webhook"

# 5. 健康检查（在 NAS 上执行，因为本机可能不在同一网段）
echo ""
echo "⏳ 等待服务启动..."
sleep 5
ssh $NAS_USER@$NAS_HOST "curl -sf http://localhost:8002/health" && echo " ✅ 服务正常" || echo " ⚠️ 服务可能尚未就绪，请稍后检查"

echo ""
echo "📋 访问地址:"
echo "   - 交易记录管理:  http://$NAS_HOST:8002/ui/"
echo "   - 策略选股:      http://$NAS_HOST:8002/ui/strategy.html"
echo "   - Trading Agent: http://$NAS_HOST:8010/stats (已部署)"
