#!/bin/bash
# 构建镜像并启动下载服务脚本
# 用法: ./scripts/build_and_serve.sh

set -e

# 配置
IMAGE_NAME="ai-lab:latest"
DOCKERFILE="docker/Dockerfile"
OUTPUT_DIR="/tmp"
OUTPUT_TAR="${OUTPUT_DIR}/ai-lab-latest.tar"
OUTPUT_TAR_GZ="${OUTPUT_DIR}/ai-lab-latest.tar.gz"
DOWNLOAD_PORT=9999

echo "========================================"
echo "构建 Docker 镜像"
echo "========================================"

# 构建镜像
DOCKER_BUILDKIT=0 docker build --pull=false -f ${DOCKERFILE} -t ${IMAGE_NAME} .

echo ""
echo "========================================"
echo "导出镜像"
echo "========================================"

# 导出镜像
docker save ${IMAGE_NAME} -o ${OUTPUT_TAR}

# 压缩
gzip -c ${OUTPUT_TAR} > ${OUTPUT_TAR_GZ}

# 显示文件大小
echo ""
echo "镜像导出完成:"
ls -lh ${OUTPUT_TAR} ${OUTPUT_TAR_GZ}

echo ""
echo "========================================"
echo "启动下载服务"
echo "========================================"

# 检查端口是否被占用
if lsof -Pi :${DOWNLOAD_PORT} -sTCP:LISTEN -t >/dev/null 2>&1; then
    echo "端口 ${DOWNLOAD_PORT} 已被占用，尝试停止现有服务..."
    PID=$(lsof -ti :${DOWNLOAD_PORT})
    kill ${PID} 2>/dev/null || true
    sleep 2
fi

# 启动 HTTP 下载服务
cd ${OUTPUT_DIR} && python3 -m http.server ${DOWNLOAD_PORT} &
DOWNLOAD_PID=$!

echo "下载服务已启动 (PID: ${DOWNLOAD_PID})"
echo "下载地址: http://$(hostname -I 2>/dev/null | awk '{print $1}' || echo 'localhost'):${DOWNLOAD_PORT}/${OUTPUT_TAR_GZ##*/}"
echo ""
echo "按 Ctrl+C 停止服务"

# 等待服务
wait ${DOWNLOAD_PID}
