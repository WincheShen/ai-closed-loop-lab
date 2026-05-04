# AI Closed Loop Lab — Developer Guide

本文档面向开发者，介绍代码结构、开发流程以及如何扩展系统。

---

# 一、项目结构

核心代码位于：

src/

主要模块：

platform/

AI 平台基础设施

central_brain/

EventBus

Workflow Engine

Event Monitor


content_ai/

内容生成 Agent

TopicGeneratorAgent

TradeContentAgent


investment_ai/

未来扩展的投资 AI


feedback_system/

策略反馈系统


services/

实际运行的服务

stock_analyzer

trading_agent_service

webhook_listener

social_media_dispatcher


infra/

基础设施代码

config

logger

model adapter


scripts/

系统运行脚本

start_all.sh

run_daily_workflow.py

run_event_monitor.py


---

# 二、核心组件

## EventBus

路径：

src/platform/central_brain/event_bus

功能：

发布事件

订阅事件

记录 SQLite Event Log


事件发布示例：

```
event_bus.publish(
    "daily.picks.generated",
    payload
)
```


事件订阅示例：

```
event_bus.subscribe(
    "trade.record.created",
    handler
)
```


---

## Workflow Engine

路径：

src/platform/central_brain/workflow_engine


用于执行系统流程。

示例：

```
workflow = Workflow("daily_market")

@workflow.step("scan")
def step(ctx):
    ...
```


---

# 三、Agent 设计

Agent 是系统的核心执行单元。

每个 Agent：

监听某个事件

执行逻辑

发布新事件


示例：

TopicGeneratorAgent

监听：

daily.picks.generated

生成内容任务。


TradeContentAgent

监听：

trade.record.created

生成交易复盘内容。


---

# 四、开发新 Agent

步骤：

1 创建 Agent 文件

src/platform/content_ai/


2 实现 handler

```
def handle_event(event):
    ...
```


3 注册 listener

```
event_bus.subscribe(
    "event.type",
    handler
)
```


---

# 五、开发流程

推荐开发流程：

1 启动系统

./scripts/start_all.sh


2 运行 workflow

python scripts/run_daily_workflow.py


3 查看事件

http://localhost:8010/events/recent


4 调试 agent


---

# 六、代码规范

建议遵循：

每个 Agent 单独模块

事件命名统一

避免跨模块强耦合


---

# 七、未来扩展

系统未来可以扩展：

Strategy Mining Agent

Portfolio Agent

Engagement Analyzer


---

# 八、调试技巧

调试系统时建议：

查看 Event Monitor


查看 SQLite Event Log


检查 agent 是否订阅事件
