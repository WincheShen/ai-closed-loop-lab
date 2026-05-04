---
description: AI闭环实验室初始化流程
tags: [setup, init, onboarding]
---

# AI 闭环实验室 — 初始化工作流

## 1. 环境准备（conda）

```bash
cd /Users/neo/Projects/ai-closed-loop-lab

# 创建独立 env（与已有 ai_agent / tradingagents 隔离）
conda create -n ai-lab python=3.11 -y
conda activate ai-lab

# 安装项目（editable）
pip install -e ".[dev]"
```

> 详细操作（含后台守护、故障排查）见 [`docs/operations.md`](../../docs/operations.md)。
> 不用 conda 想用 venv：`python3.11 -m venv .venv && source .venv/bin/activate && pip install -e ".[dev]"`

## 2. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env，填入：
#   - OPENAI_API_KEY 或 ANTHROPIC_API_KEY
#   - TRADING_MODE=mock (初次运行)
```

## 3. 首次运行测试

```bash
# 仅运行探索者扫描
python scripts/run_explorer.py --mode scan

# 运行完整模拟盘闭环
python scripts/run_full_loop.py --mode mock
```

## 4. 接入子项目

```bash
# 链接现有的 Social-media-automation
cd third_party/social_media_automation
git clone /path/to/Social-media-automation .
# 或创建符号链接
# ln -s /path/to/Social-media-automation third_party/social_media_automation

# 链接 TradingAgents
cd third_party/tradingAgents_neo
git clone /path/to/tradingAgents_neo .
```

## 5. 启动定时调度

```bash
# 前台运行（调试）
python scripts/scheduler.py

# 或使用 Docker
docker compose up -d
```
