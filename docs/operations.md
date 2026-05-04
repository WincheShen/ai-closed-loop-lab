# 操作手册 — 启动 / 停止 / 日常运维

> 工具栈：**conda**（miniforge 自带）+ pip
> （mamba 也可用，但需 `export MAMBA_ROOT_PREFIX=/opt/homebrew/Caskroom/miniforge/base` 才能看到已有 env）
> Python 版本：3.11
> 项目根：`/Users/neo/Projects/ai-closed-loop-lab`

## 0. 一次性环境初始化（首次部署做）

```bash
cd /Users/neo/Projects/ai-closed-loop-lab

# 1. 创建独立 env（与已有 ai_agent / tradingagents 隔离）
conda create -n ai-lab python=3.11 -y

# 2. 激活
conda activate ai-lab

# 3. 安装本项目（editable 模式，改代码无需重装）
pip install -e ".[dev]"
```

> 如果 `akshare` / `qlib` 装得很慢或失败：
> ```bash
> pip install -e ".[dev]" -i https://pypi.tuna.tsinghua.edu.cn/simple
> ```
> 或临时跳过它们（akshare 不装时会自动走 mock）：
> ```bash
> pip install fastapi uvicorn pydantic pyyaml httpx pandas pytest -i https://pypi.tuna.tsinghua.edu.cn/simple
> pip install -e . --no-deps
> ```

## 0.1 启动入口速查表

> 新旧两套入口并存（Phase 3.5 过渡状态）。新代码默认用 **推荐入口**，旧入口仅为兼容保留。
> 架构背景见 [architecture.md §0](./architecture.md#0-新旧模块映射phase-35--phase-4-过渡状态)。

| 用途 | 推荐入口 | 端口 | 备用入口（等价） |
|------|---------|------|----------------|
| TradingAgent 分析服务 | `scripts/run_trading_agent_service.py` | 8001 | — |
| Webhook 接收（交易记录） | `scripts/run_webhook_listener.py` | 8002 | — |
| SMA Dispatcher CLI | `scripts/dispatch_to_sma.py` | — | — |
| 每日选股（直接跑流水线） | `scripts/run_daily_scan.py` | — | — |
| 每日选股（事件驱动编排） | `scripts/run_daily_workflow.py` | — | — |
| 事件监控 API | `python -m ai_platform.cli event-monitor` | 8010 | `scripts/run_event_monitor.py` |
| 策略反馈指标 API | `python -m ai_platform.cli strategy-metrics` | 8011 | `scripts/run_strategy_metrics.py` |

> **原则**：生产/日常运维都用推荐入口；备用入口保留是为了旧脚本/cron 不用立刻改。
> 两组脚本背后跑的是同一份 Python 代码，**不要同时跑同一种服务**（避免端口冲突）。

## 1. 启动 TradingAgent 服务

```bash
conda activate ai-lab
cd /Users/neo/Projects/ai-closed-loop-lab

# Phase 1 强制走 mock 分析器
TAS_ANALYZER=mock python scripts/run_trading_agent_service.py

# Phase 3 切换到真实 TradingAgents（需先 pip install -e /Users/neo/Projects/tradingAgents_neo）
# 详见 docs/phase3.md
TAS_ANALYZER=tradingagents python scripts/run_trading_agent_service.py
```

成功标志：
```
INFO: Uvicorn running on http://127.0.0.1:8001
INFO: TradingAgent Service started: analyzer=mock
```

**保持此终端运行**。停服务：`Ctrl+C`。

### 后台守护方式（可选）

前台调试用上面的方式。要让它在后台跑：

```bash
# 用 nohup（最简单）
nohup env TAS_ANALYZER=mock python scripts/run_trading_agent_service.py \
  > /tmp/trading_agent.log 2>&1 &
echo $! > /tmp/trading_agent.pid

# 查日志
tail -f /tmp/trading_agent.log

# 停服务
kill $(cat /tmp/trading_agent.pid)
```

## 2. 验证服务

新开一个终端：

```bash
conda activate ai-lab

# 健康检查
curl http://127.0.0.1:8001/health

# 触发一次分析
curl -X POST http://127.0.0.1:8001/analyze \
  -H "Content-Type: application/json" \
  -d '{"symbol":"600519","depth":"deep"}' | python -m json.tool

# 再请求一次同一只 → cache_hit=true，elapsed 接近 0
curl -X POST http://127.0.0.1:8001/analyze \
  -H "Content-Type: application/json" \
  -d '{"symbol":"600519"}' | python -m json.tool

# 看缓存统计
curl http://127.0.0.1:8001/stats
```

浏览器打开 **http://127.0.0.1:8001/docs** 是 FastAPI 自动生成的 Swagger UI，可点击调用。

## 3. 跑每日选股流水线

```bash
conda activate ai-lab
cd /Users/neo/Projects/ai-closed-loop-lab

# 与 TradingAgent 服务联动（需先启动 :8001）
python scripts/run_daily_scan.py

# 不调用 Agent，仅跑规则
python scripts/run_daily_scan.py --no-agent

# 自定义 Agent URL / 限制 Agent 调用次数
python scripts/run_daily_scan.py --agent-url http://127.0.0.1:8001 --max-agent-calls 5
```

输出：
- 终端：激进推荐 + 稳健推荐 + 候选池
- 文件：`data/daily_picks/YYYY-MM-DD.json`

## 4. 启动 Webhook Listener

```bash
conda activate ai-lab
python scripts/run_webhook_listener.py
# 监听 http://127.0.0.1:8002
```

测试发送交易记录：
```bash
# 纯文字
curl -X POST http://127.0.0.1:8002/webhook/trade \
  -F "text=今天买入贵州茅台，建仓1500元" \
  -F "source=manual"

# 文字 + 图片
curl -X POST http://127.0.0.1:8002/webhook/trade \
  -F "text=持仓截图，已上车" \
  -F "image=@/path/to/screenshot.png"

# 查看最近记录
curl http://127.0.0.1:8002/webhook/records/recent | python -m json.tool
```

## 5. 跑测试

```bash
conda activate ai-lab
cd /Users/neo/Projects/ai-closed-loop-lab
pytest -v
```

## 5b. Phase 2：把选题推送到 Social Media

> 详见下方 §11。

## 6. 端口约定

| 服务 | 端口 | 配置变量 | 所在仓库 |
|------|------|----------|---------|
| TradingAgent Service | 8001 | `TAS_PORT` | ai-closed-loop-lab |
| Webhook Listener | 8002 | `WEBHOOK_PORT` | ai-closed-loop-lab |
| SMA API Receiver | 8003 | `SMA_API_PORT` | Social-media-automation |

## 7. 数据落盘位置

```
data/
├── trading_agent_service/
│   ├── cache.sqlite              # 报告元数据
│   └── reports/{symbol}_{date}.json   # 报告全文
├── daily_picks/
│   └── 2026-04-26.json           # 每日选股结果
└── webhook/
    ├── trade_records.sqlite      # 交易记录索引
    ├── raw/{record_id}.png       # 原始图片
    └── redacted/{record_id}_safe.png  # 脱敏图片
```

清空全部数据（小心）：
```bash
rm -rf data/trading_agent_service data/daily_picks data/webhook
```

## 8. 常用 conda 命令速查

```bash
# 列出所有 env
conda env list

# 激活 / 退出
conda activate ai-lab
conda deactivate

# 删除 env（回收空间）
conda env remove -n ai-lab

# 导出 env（备份）
conda env export -n ai-lab --no-builds > environment.yml

# 升级 ai-lab 内某个包
conda activate ai-lab
pip install --upgrade fastapi
```

## 9. 故障排查

### 端口被占
```bash
lsof -i :8001     # 看谁占了
kill -9 <PID>
```

### akshare 拉取失败
- 现象：日志出现 `akshare 真实拉取失败：...，降级到 mock`
- 原因：网络 / 代理 / 接口变更
- 处理：Phase 1 mock 数据可继续跑通流水线，不影响功能验证

### Cache 命中率异常低
```bash
curl http://127.0.0.1:8001/stats
# 若 cache_hit_rate < 30%，检查 reevaluation_price_range 是否过窄
# Phase 1 默认 ±5%，可在 MockAnalyzer/真实 Analyzer 中调整
```

### Pydantic 报错 "tuple[float, float]"
- 原因：用了 Python <3.9
- 处理：必须 ≥ 3.11（pyproject 要求）

## 11. Phase 2 — 跨仓库联动（ai-lab ↔ Social-media-automation）

### 架构
```
[Stock Analyzer] ──► daily_picks*.json
                        │
                        ▼
                 [TopicRouter] ── HTTP POST ──► [SMA API :8003]
                                                    │
                                                    ▼
                                            create_task → LangGraph
                                            (analyst → research → creative ...)
```

### A. SMA 端首次配置

```bash
cd /Users/neo/Projects/Social-media-automation
conda activate <你的-sma-env>      # SMA 自己的 env
pip install -e ".[api]"            # 装 fastapi + uvicorn
```

### B. 启动 SMA API（终端 D）

```bash
cd /Users/neo/Projects/Social-media-automation
python scripts/run_api_server.py
# 监听 http://127.0.0.1:8003
# Swagger UI: http://127.0.0.1:8003/docs
```

健康检查：
```bash
curl http://127.0.0.1:8003/health
# 返回 {accounts: ["XHS_01", "XHS_02", ...]}
```

### C. ai-lab 端推送（三种触发方式）

**1) 用最新的 daily_picks 推送**
```bash
conda activate ai-lab
cd /Users/neo/Projects/ai-closed-loop-lab
python scripts/dispatch_to_sma.py from-picks --account XHS_01
```

**2) daily_scan 跑完自动推送**
```bash
python scripts/run_daily_scan.py --sma-account XHS_01
# 顺带把激进+稳健推荐脱敏后推到 SMA
```

**3) 人工选题（FR-2.5 二次加工模式）**
```bash
python scripts/dispatch_to_sma.py manual \
  --account XHS_01 \
  --text "今天聊聊低空经济板块的中线机会"
```

### D. 在 SMA 端查看任务

```bash
# 列最近任务
curl http://127.0.0.1:8003/api/tasks?limit=10 | python -m json.tool

# 查单个任务详情（含 LangGraph 跑完后的 draft_title / draft_content）
curl http://127.0.0.1:8003/api/tasks/<task_id> | python -m json.tool
```

也可以打开 SMA 的 Web Admin（如果在跑）查看草稿。

### E. 合规保证

ai-lab 在推送前已做：
- ✂️ 股票代码 `600519` → `60xxxx`
- ✂️ 名称 `贵州茅台` → `贵X台`
- ✂️ 推荐理由复用 Webhook 端的 `sanitize_text` 替换
- ❌ 不传精确买入价 / 仓位 / 账户金额

SMA 端也会再过一道 `safety_check` 节点，双层兜底。

### F. 环境变量速查

| 变量 | 默认 | 用途 |
|------|------|------|
| `SMA_BASE_URL` | `http://127.0.0.1:8003` | ai-lab 端 SmaClient 的目标 |
| `SMA_API_TOKEN` | （空） | 可选 Bearer token |
| `SMA_API_PORT` | 8003 | SMA 端监听端口 |
| `SMA_API_HOST` | 127.0.0.1 | SMA 端监听地址 |

## 10. venv 备选方案（如果不想用 conda）

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
# 后续命令把 `conda activate ai-lab` 替换成 `source .venv/bin/activate` 即可
```
