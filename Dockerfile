# 使用 Python 3.11 瘦镜像
FROM python:3.11-slim

WORKDIR /app

# 安装 Git（用于安装本地 editable 模块）
RUN apt-get update && apt-get install -y git && rm -rf /var/lib/apt/lists/*

# 1. 先拷贝并安装核心依赖库 tradingAgents_neo
# 假设你在 CI 过程中会将它放在 libs 目录下
COPY ./libs/tradingAgents_neo /app/libs/tradingAgents_neo
RUN pip install -e /app/libs/tradingAgents_neo

# 2. 安装 ai-closed-loop-lab 的依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 3. 拷贝本项目代码
COPY . .

# 修正导入路径：将 src 加入 PYTHONPATH
ENV PYTHONPATH="/app/src:/app/libs/tradingAgents_neo/src:${PYTHONPATH}"

# 暴露分析服务端口
EXPOSE 8001

# 启动服务
CMD ["python", "scripts/run_trading_agent_service.py", "--host", "0.0.0.0", "--port", "8001"]
