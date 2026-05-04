# AI Closed Loop Lab — Agent Catalog

本文档列出系统中的所有 Agent、监听事件以及执行逻辑。

---

# 一、Agent 设计原则

系统采用 Agent 架构：

每个 Agent：

监听某个事件

执行任务

可能产生新的事件


Agent 之间通过 EventBus 解耦。


---

# 二、Agent 列表

## TopicGeneratorAgent

路径：

src/platform/content_ai/topic_generation/topic_generator_agent.py


监听事件：

daily.picks.generated


功能：

将 Daily Picks 转换为内容选题。


执行流程：

Daily Picks

→ TopicRouter

→ Social Media Automation


输出：

内容任务


---

## TradeContentAgent

路径：

src/platform/content_ai/topic_generation/trade_content_agent.py


监听事件：

trade.record.created


功能：

根据交易记录生成复盘内容。


执行流程：

Trade Record

→ TopicRouter

→ Social Media Automation


输出：

交易复盘内容任务


---

# 三、未来 Agent

系统未来可以增加：


StrategyFeedbackAgent

监听：

trade.record.created

daily.picks.generated


分析：

策略表现



EngagementAgent

监听：

content.published

engagement.updated


分析：

用户互动


---

# 四、Agent 开发流程

新增 Agent 步骤：


1 创建 Agent 文件

src/platform/


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


4 更新文档

更新：

agent_catalog.md

event_catalog.md


---

# 五、Agent 设计建议

保持单一职责

避免复杂耦合

通过事件通信
