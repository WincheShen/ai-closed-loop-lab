# AI Closed Loop Lab — Docker 镜像
# 用于部署运行定时调度器（日常扫描 + 周末复盘）

FROM python:3.11-slim

WORKDIR /app

# 安装系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libsqlite3-dev \
    && rm -rf /var/lib/apt/lists/*

# 复制项目文件
COPY pyproject.toml ./
COPY src/ ./src/
COPY config/ ./config/
COPY scripts/ ./scripts/
COPY README.md ./

# 安装 Python 依赖
RUN pip install --no-cache-dir -e "."

# 创建数据目录
RUN mkdir -p data/logs data/memory data/state

# 默认运行调度器
CMD ["python", "scripts/scheduler.py"]
