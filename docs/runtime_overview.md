# AI Closed Loop Lab — Runtime Overview

本文件描述系统在**运行时（runtime）**的实际组件关系，帮助开发者快速理解：

- 哪些服务在运行
- 它们之间如何通信
- 事件在系统中如何流动

---

# 1 运行中的服务

本地开发通常运行以下服务：

TradingAgent Service

端口: 8001

职责:

AI 投研分析

股票深度分析报告


Webhook Listener

端口: 8002

职责:

接收交易记录

文字合规处理

图片脱敏

发布事件 trade.record.created


Event Monitor

端口: 8010

职责:

查看系统事件日志

调试系统事件流


Workflow Runner

脚本触发:

scripts/run_daily_workflow.py

职责:

执行每日选股流程

发布事件 daily.picks.generated


---

# 2 Runtime 架构图

运行时结构：

Market Data

   │

   ▼

Daily Workflow

   │

   ▼

EventBus

   │

   ├── TopicGeneratorAgent

   │       │

   │       ▼

   │   Social Media Automation

   │

   └── TradeContentAgent

           │

           ▼

       Social Media Automation


---

# 3 EventBus

EventBus 是系统的核心通信机制。

职责:

发布事件

分发事件

记录事件日志


所有事件会写入:

```
data/event_bus/events.sqlite
```


---

# 4 关键事件流

## 投研 → 内容

Daily Workflow

→ daily.picks.generated

→ TopicGeneratorAgent

→ 内容任务


## 交易 → 内容

Webhook Listener

→ trade.record.created

→ TradeContentAgent

→ 内容任务


---

# 5 Runtime 数据

系统运行时产生的数据：

Daily Picks

```
data/daily_picks/
```

Trade Records

```
data/webhook/
```

Event Log

```
data/event_bus/events.sqlite
```


---

# 6 典型运行流程

开发者常见操作：

启动系统

```
./scripts/start_all.sh
```


触发每日选股

```
python scripts/run_daily_workflow.py
```


查看事件

```
http://localhost:8010/events/recent
```


发送交易记录

```
curl -X POST http://localhost:8002/webhook/trade
```


---

# 7 设计特点

系统具有以下特点：


Event-driven

模块之间通过事件通信


Loose coupling

Agent 之间无直接依赖


Simple runtime

无需 Kafka / Redis


Local-first

适合单机运行


---

# 8 未来扩展

系统未来可以增加：

Strategy Feedback Agent

Engagement Analyzer

Portfolio Agent


形成完整闭环：

Market

→ Investment

→ Content

→ Audience

→ Feedback

→ Strategy Optimization
