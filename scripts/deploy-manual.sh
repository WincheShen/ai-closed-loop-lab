#!/bin/bash
# NAS 手动部署指南 - 无需 SSH 免密
# 用法: 复制这些命令到 NAS 终端执行

set -e

NAS_HOST=${1:-192.168.3.73}
NAS_USER=${2:-kingsy_9}
PROJECT_DIR="/volume1/docker/ai-lab-web"

echo "=========================================="
echo "NAS 手动部署步骤"
echo "=========================================="
echo ""
echo "请在 NAS 上执行以下命令："
echo ""
echo "1. 创建目录:"
echo "   mkdir -p $PROJECT_DIR/{data,logs}"
echo ""
echo "2. 确保 .env 文件存在（稍后 scp 上传）"
echo ""
echo "按回车继续上传文件..."
read

# 上传文件（会提示输入密码）
echo "📤 上传 docker-compose.nas.yml..."
scp docker/docker-compose.nas.yml $NAS_USER@$NAS_HOST:$PROJECT_DIR/

if [ -f .env ]; then
    echo "📤 上传 .env 文件..."
    scp .env $NAS_USER@$NAS_HOST:$PROJECT_DIR/
fi

echo ""
echo "✅ 文件上传完成！"
echo ""
echo "现在请在 NAS 终端执行："
echo ""
cat << EOF
ssh $NAS_USER@$NAS_HOST
cd $PROJECT_DIR
docker pull ghcr.io/wincheshen/ai-closed-loop-lab/trading-agent:latest
docker-compose -f docker-compose.nas.yml up -d webhook
docker ps | grep ai-lab
EOF

echo ""
echo "部署完成后访问:"
echo "  http://$NAS_HOST:8002/ui/"
echo "  http://$NAS_HOST:8002/ui/strategy.html"
