#!/bin/bash
# NAS 部署完整步骤（在 NAS 终端执行）
# 解决 Docker 权限问题的完整方案

echo "=========================================="
echo "NAS Docker 权限解决方案"
echo "=========================================="
echo ""
echo "方案 1: 每次用 sudo 运行 Docker（推荐快速部署）"
echo "方案 2: 将当前用户加入 docker 组（永久解决，需重新登录）"
echo ""

PROJECT_DIR="/volume1/docker/ai-lab-web"

echo "=========================================="
echo "步骤 1: 创建目录"
echo "=========================================="
mkdir -p $PROJECT_DIR/{data,logs}
echo "✅ 目录创建完成: $PROJECT_DIR"
echo ""

echo "=========================================="
echo "步骤 2: 配置 Docker 权限"
echo "=========================================="
echo ""
echo "请选择:"
echo "  [1] 临时用 sudo 运行（快速开始）"
echo "  [2] 将当前用户加入 docker 组（永久解决）"
echo ""
read -p "选择 [1]: " choice
choice=${choice:-1}

if [ "$choice" = "2" ]; then
    echo "添加当前用户到 docker 组..."
    sudo synogroup --member docker $(whoami) 2>/dev/null || sudo usermod -aG docker $(whoami)
    echo "✅ 已添加，需要重新登录生效"
    echo "   请执行: exit"
    echo "   然后重新 SSH 连接"
    echo ""
    echo "重新登录后，Docker 命令将不再需要 sudo"
    exit 0
fi

echo "使用 sudo 运行 Docker..."
echo ""

echo "=========================================="
echo "步骤 3: 下载 docker-compose 配置"
echo "=========================================="
cd $PROJECT_DIR
curl -fsSL -o docker-compose.nas.yml \
  https://raw.githubusercontent.com/WincheShen/ai-closed-loop-lab/main/docker/docker-compose.nas.yml

if [ $? -ne 0 ]; then
    echo "❌ 下载失败，请手动上传 docker-compose.nas.yml 到 $PROJECT_DIR/"
    echo "   从 https://github.com/WincheShen/ai-closed-loop-lab/blob/main/docker/docker-compose.nas.yml 下载"
    exit 1
fi
echo "✅ 配置下载完成"
echo ""

echo "=========================================="
echo "步骤 4: 检查 .env 文件"
echo "=========================================="
if [ ! -f .env ]; then
    echo "⚠️  .env 文件不存在"
    echo ""
    echo "请手动创建 .env 文件："
    echo "   vi $PROJECT_DIR/.env"
    echo ""
    echo "最小配置内容："
    cat << 'EOF'
OPENAI_API_KEY=your-key-here
OPENAI_BASE_URL=https://ai-yhuang96usai172233441975.openai.azure.com/openai/v1
OPENAI_API_VERSION=2025-03-01-preview
DB_PATH=/app/data/central_brain.db
WEBHOOK_AUTO_SMA_DISPATCH=false
EOF
    echo ""
    read -p "按回车继续（跳过 .env 检查）..."
else
    echo "✅ .env 文件已存在"
fi
echo ""

echo "=========================================="
echo "步骤 5: 拉取镜像并启动"
echo "=========================================="
echo "执行: sudo docker-compose -f docker-compose.nas.yml up -d webhook"
echo ""
sudo docker-compose -f docker-compose.nas.yml up -d webhook

echo ""
echo "=========================================="
echo "步骤 6: 检查状态"
echo "=========================================="
sleep 2
echo "容器状态:"
sudo docker ps | grep ai-lab-webhook || echo "❌ 容器未运行"
echo ""
echo "服务健康检查:"
curl -s http://localhost:8002/health 2>/dev/null || echo "⏳ 服务启动中..."
echo ""

echo "=========================================="
echo "✅ 部署完成！"
echo "=========================================="
echo ""
echo "访问地址:"
echo "  交易记录管理: http://192.168.3.73:8002/ui/"
echo "  策略选股:     http://192.168.3.73:8002/ui/strategy.html"
echo "  Trading Agent: http://192.168.3.73:8010/stats (已有)"
echo ""
echo "查看日志:"
echo "  sudo docker logs ai-lab-webhook -f"
echo ""
echo "停止服务:"
echo "  cd $PROJECT_DIR && sudo docker-compose -f docker-compose.nas.yml down"
