# AI Closed Loop Lab — System Architecture

本文档描述系统整体架构以及事件驱动设计。

> **注意（Phase 3.5 过渡状态）**：本文档描述的是 Phase 4 目标架构（事件驱动 + 五大子系统）。
> 当前代码库中 **Phase 1-3 的业务模块（`stock_analyzer`、`trading_agent_service`、
> `social_media_dispatcher`、`webhook_listener`）与 Phase 4 的 `ai_platform/*` 编排层并行存在**。
> 新旧模块映射、依赖方向规则、标准启动入口请参见 [architecture.md §0](./architecture.md#0-新旧模块映射phase-35--phase-4-过渡状态)。

---

# 一、总体架构

系统由以下几个部分组成：

Market Data

→ Investment AI

→ EventBus

→ Agents

→ Content System

→ Feedback Loop


核心思想：

Event Driven Architecture


---

# 二、系统组件

## Investment Layer

负责市场分析与选股。

组件：

Stock Analyzer

TradingAgent


输出：

daily.picks.generated


---

## Event Layer

核心组件：

EventBus


功能：

发布事件

订阅事件

记录事件日志


事件日志存储：

SQLite


路径：

data/event_bus/events.sqlite


---

## Agent Layer

Agent 负责消费事件并执行逻辑。


TopicGeneratorAgent

监听：

daily.picks.generated

生成：

内容选题


TradeContentAgent

监听：

trade.record.created

生成：

交易复盘内容


---

## Content Layer

由 Social Media Automation 系统负责。


流程：

Topic

→ Research

→ Draft

→ Safety

→ Publish


---

# 三、事件流

## 选股流程

Daily Workflow

→ daily.picks.generated

→ TopicGeneratorAgent

→ Content Task


## 交易记录流程

Webhook Listener

→ trade.record.created

→ TradeContentAgent

→ Content Task


---

# 四、数据存储

主要数据：


Daily Picks

路径：

data/daily_picks/


Trade Records

路径：

data/webhook/


Event Log

路径：

data/event_bus/events.sqlite


---

# 五、系统监控

Event Monitor

端口：

8010


API：

/events/recent


用于查看系统最近事件。


---

# 六、未来架构扩展

系统未来可以扩展：

Feedback Agents

Engagement Analyzer

Strategy Optimizer


形成完整闭环：

Market

→ Investment

→ Content

→ Audience

→ Feedback

→ Strategy Optimization
