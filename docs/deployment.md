# AI Closed Loop Lab — 部署指南

## 系统架构

```
┌─────────────────────────────────────────────────────────────────┐
│                     React Frontend (:5173 / :8002)               │
│    Dashboard | 策略选股 | Agent日报 | 工作流 | 社媒管线 | 记录    │
└───────────────────────────────┬─────────────────────────────────┘
                                │ HTTP
┌───────────────────────────────┴─────────────────────────────────┐
│                    FastAPI Backend (:8002)                       │
│                                                                 │
│  /api/strategy/*     策略选股 (NL→指标→执行)                     │
│  /api/agent-report/* Agent日报 (选股/评估/风控/执行)              │
│  /api/stock/*        个股分析代理                                │
│  /api/social-posts   社媒发布管理                                │
│  /webhook/trade      交易记录接收                                │
│  /health             健康检查                                   │
└─────────────────────────────────────────────────────────────────┘
                                │
        ┌───────────────────────┼────────────────────┐
        ▼                       ▼                    ▼
┌──────────────┐   ┌────────────────────┐   ┌──────────────┐
│  Scheduler   │   │    SQLite DB       │   │     SMA      │
│ (定时任务)    │   │ central_brain.db   │   │  (:8003)     │
│              │   │ + daily_picks/*.json│   │ 社媒自动化   │
└──────────────┘   └────────────────────┘   └──────────────┘
```

### 端口约定

| 端口 | 服务 | 说明 |
|------|------|------|
| 5173 | Vite Dev Server | 仅本地开发用，代理到 8002 |
| 8002 | Webhook Listener | 核心服务 (API + 生产环境含 React UI) |
| 8001 | TradingAgent | 单股深度分析 (可选) |
| 8003 | SMA | Social Media Automation (可选) |

---

## 1. 本地开发

### 前置条件

```bash
# Python 3.11+
conda create -n ai-lab python=3.11 -y
conda activate ai-lab

# 安装依赖
cd ~/Projects/ai-closed-loop-lab
pip install -e ".[dev]"

# Node.js 18+ (前端)
cd frontend
npm install
```

### 环境变量

复制 `.env.example` → `.env`，至少配置:

```env
OPENAI_API_KEY=sk-xxx
OPENAI_BASE_URL=https://your-endpoint/openai/v1
```

### 一键启动

```bash
./scripts/start_all.sh
```

启动后:
- **前端**: http://localhost:5173 (Vite, 热更新)
- **后端 API**: http://localhost:8002
- **TradingAgent**: http://localhost:8001 (mock 模式)

### 常用命令

```bash
./scripts/dev up           # 启动所有服务
./scripts/dev down         # 停止所有服务
./scripts/dev scan         # 执行一次选股扫描
./scripts/dev workflow     # 执行完整 Agent 工作流
./scripts/dev report       # 查看 Agent 日报日期列表

# 仅启动后端（不启动前端）
./scripts/start_all.sh --backend-only

# 手动执行选股
PYTHONPATH=src python scripts/run_daily_scan.py

# 手动执行完整工作流 (MarketBrain → 选股 → Strategist → RiskGovernor → Executor)
PYTHONPATH=src python scripts/run_daily_workflow.py
```

### 前端单独开发

```bash
cd frontend
npm run dev    # Vite 开发服务器 :5173
npm run build  # 构建到 frontend/dist/
```

---

## 2. 群晖 NAS Docker 部署

### 2.1 首次部署

#### Step 1: SSH 登录 NAS

```bash
ssh kingsy_9@192.168.3.73
```

#### Step 2: 创建目录

```bash
sudo mkdir -p /volume1/docker/ai-lab/{data,logs}
cd /volume1/docker/ai-lab
```

#### Step 3: 上传配置文件

从本机推送:

```bash
# 在本机执行
scp docker/docker-compose.nas.yml kingsy_9@192.168.3.73:/volume1/docker/ai-lab/docker-compose.yml
scp .env kingsy_9@192.168.3.73:/volume1/docker/ai-lab/.env
```

或者在 NAS 上下载:

```bash
curl -fsSL -o docker-compose.yml \
  https://raw.githubusercontent.com/WincheShen/ai-closed-loop-lab/main/docker/docker-compose.nas.yml
```

#### Step 4: 配置 .env

```bash
vi /volume1/docker/ai-lab/.env
```

最小配置:

```env
OPENAI_API_KEY=your-key-here
OPENAI_BASE_URL=https://your-endpoint/openai/v1
OPENAI_API_VERSION=2025-03-01-preview
```

#### Step 5: 登录 GHCR (首次)

```bash
echo "YOUR_GITHUB_PAT" | sudo docker login ghcr.io -u WincheShen --password-stdin
```

#### Step 6: 启动服务

```bash
cd /volume1/docker/ai-lab

# 仅 Web 服务 (含 React UI + 所有 API)
sudo docker-compose up -d web

# Web + 定时调度器
sudo docker-compose --profile scheduler up -d

# 全部 (含 SMA)
sudo docker-compose --profile scheduler --profile sma up -d
```

#### Step 7: 验证

```bash
curl http://localhost:8002/health
# {"status":"ok","service":"webhook_listener"}

curl http://localhost:8002/api/agent-report/dates
# ["2026-05-24","2026-05-03",...]
```

**访问地址**: http://192.168.3.73:8002

---

### 2.2 更新部署

推送代码到 GitHub main 分支后，CI 自动构建镜像。NAS 上拉取最新:

```bash
cd /volume1/docker/ai-lab
sudo docker-compose pull
sudo docker-compose up -d web
# 如有 scheduler:
sudo docker-compose --profile scheduler up -d
```

或用一键脚本 (本机执行):

```bash
./scripts/deploy-to-nas.sh
```

---

### 2.3 Scheduler 调度表

定时调度器 (`scheduler` 容器) 在 A 股交易日自动执行:

| 时间 | 任务 | 说明 |
|------|------|------|
| 09:35 | MarketBrain 判定 | 开盘后首次市场制度判断 |
| 09:30-14:30 每30min | 持仓复审 | 盘中持仓 HOLD/SELL 决策 |
| 11:35 | MarketBrain 午盘 | 午盘后重新判定 |
| 14:00 | MarketBrain 尾盘 | 尾盘前重新判定 |
| 15:05 | 收盘分析 | 生成社媒发布内容 |
| 15:35 | 每日选股 | 收盘后全市场扫描 |
| 15:40 | 模拟盘闭环 | 执行模拟交易 |
| 周日 20:00 | 周复盘 | 策略绩效回顾 |

---

### 2.4 SMA 社媒自动化

Social Media Automation 是独立项目，通过 HTTP API 集成:

```bash
# 手动推送选题到 SMA
PYTHONPATH=src python scripts/dispatch_to_sma.py from-picks --account XHS_01

# 同步 SMA 互动数据回 ai-lab
PYTHONPATH=src python scripts/sync_sma_engagements.py
```

在 NAS 上，SMA 容器和 ai-lab-web 容器通过 Docker 网络通信。

---

## 3. 维护手册

### 查看日志

```bash
# NAS 上
sudo docker-compose logs -f web
sudo docker-compose logs -f scheduler

# 本地
tail -f logs/scheduler.log
```

### 数据备份

重要数据在 `/volume1/docker/ai-lab/data/` 下:

```
data/
├── central_brain.db          # 核心数据库 (regime/signals/orders/...)
├── daily_picks/              # 每日选股 JSON
│   └── 2026-05-24.json
├── webhook/
│   └── trade_records.sqlite  # 交易记录
└── strategies.db             # 保存的策略
```

建议定期备份 `central_brain.db` 和 `daily_picks/`。

### 故障排查

```bash
# 检查容器状态
sudo docker ps -a | grep ai-lab

# 进入容器调试
sudo docker exec -it ai-lab-web bash

# 检查端口占用
sudo lsof -i :8002

# 重启服务
sudo docker-compose restart web
```

### 手动触发选股 (NAS)

```bash
sudo docker exec ai-lab-web python scripts/run_daily_scan.py
```

---

## 4. 环境变量参考

| 变量 | 必需 | 说明 | 默认值 |
|------|------|------|--------|
| `OPENAI_API_KEY` | ✅ | LLM API Key | - |
| `OPENAI_BASE_URL` | - | API 端点 | https://api.openai.com/v1 |
| `OPENAI_API_VERSION` | - | Azure 版本号 | - |
| `QUICK_THINK_MODEL` | - | 快速思考模型 | gpt-4o-mini |
| `DEEP_THINK_MODEL` | - | 深度分析模型 | gpt-4o |
| `TAS_ANALYZER` | - | 分析器类型 | mock |
| `DB_PATH` | - | 数据库路径 | data/central_brain.db |
| `WEBHOOK_AUTO_SMA_DISPATCH` | - | 自动推送 SMA | false |
| `SMA_BASE_URL` | - | SMA 服务地址 | http://127.0.0.1:8003 |
| `TRADING_MODE` | - | 交易模式 | mock |
