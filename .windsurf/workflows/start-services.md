---
description: 启动 AI 闭环实验室 Phase1 服务（TradingAgent / Webhook / 选股流水线）
tags: [run, phase1, daily]
---

# 启动 Phase 1 服务

> 详细说明见 `docs/operations.md`。本工作流给出最常用的启动序列。

## 0. 首次部署（仅做一次）

```bash
cd /Users/neo/Projects/ai-closed-loop-lab
conda create -n ai-lab python=3.11 -y
conda activate ai-lab
pip install -e ".[dev]"
```

## 1. 启动 TradingAgent 服务（终端 A）

// turbo
```bash
conda activate ai-lab && cd /Users/neo/Projects/ai-closed-loop-lab && TAS_ANALYZER=mock python scripts/run_trading_agent_service.py
```

成功后访问 http://127.0.0.1:8001/docs 看 Swagger UI。

## 2. 启动 Webhook Listener（终端 B，可选）

// turbo
```bash
conda activate ai-lab && cd /Users/neo/Projects/ai-closed-loop-lab && python scripts/run_webhook_listener.py
```

## 3. 跑每日选股（终端 C，触发后即结束）

// turbo
```bash
conda activate ai-lab && cd /Users/neo/Projects/ai-closed-loop-lab && python scripts/run_daily_scan.py
```

带 SMA 推送（Phase 2，需要先在另一终端启 SMA API）：
```bash
python scripts/run_daily_scan.py --sma-account XHS_01
```

## 3b. 启动 SMA API 接收端（终端 D，Phase 2）

```bash
cd /Users/neo/Projects/Social-media-automation
# 首次需：pip install -e ".[api]"
python scripts/run_api_server.py
# 监听 :8003，Swagger: http://127.0.0.1:8003/docs
```

## 4. 验证

```bash
curl http://127.0.0.1:8001/health
curl http://127.0.0.1:8001/stats
curl -X POST http://127.0.0.1:8001/analyze -H 'Content-Type: application/json' -d '{"symbol":"600519"}' | python -m json.tool
```

## 5. 停止服务

在终端 A / B 按 `Ctrl+C` 即可。
