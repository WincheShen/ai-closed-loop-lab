# AI Closed Loop Lab — 统一镜像
# 包含: Webhook API + Strategy API + Agent Report API + React UI + Scheduler
#
# 构建:
#   docker build -f docker/Dockerfile -t ai-lab .
#
# 多阶段构建：先编译前端，再打包后端

# ── Stage 1: 构建 React 前端 ──
FROM node:20-slim AS frontend-build
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci --silent
COPY frontend/ .
RUN npm run build

# ── Stage 2: Python 后端 ──
FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libsqlite3-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 安装 Python 依赖
COPY pyproject.toml ./
COPY requirements.txt ./
COPY src/ ./src/
COPY config/ ./config/
COPY scripts/ ./scripts/
COPY README.md ./

RUN pip install --no-cache-dir -e "."

# 复制前端构建产物到静态目录
COPY --from=frontend-build /frontend/dist /app/frontend/dist

# 创建数据目录
RUN mkdir -p data/daily_picks data/webhook data/logs

ENV PYTHONPATH="/app/src"
ENV TZ="Asia/Shanghai"
ENV PYTHONUNBUFFERED=1

EXPOSE 8002

# 默认启动 Webhook Listener (含所有 API + 静态前端)
CMD ["python", "-m", "uvicorn", "webhook_listener.server:app", "--host", "0.0.0.0", "--port", "8002"]
