# AI Closed Loop Lab — Event Catalog

本文档列出系统所有事件类型、来源模块以及事件 payload 结构。

---

# 一、事件设计原则

系统采用事件驱动架构：

模块之间不直接调用，而是通过 EventBus 发送事件。


事件命名规则：

<domain>.<entity>.<action>

示例：

trade.record.created

daily.picks.generated


---

# 二、事件列表

## daily.picks.generated

说明：

每日选股完成后触发。


事件来源：

Daily Workflow


消费者：

TopicGeneratorAgent


payload 示例：

{
  "date": "2026-05-01",
  "num_candidates": 23,
  "picks": {...}
}


---

## trade.record.created

说明：

Webhook Listener 接收到交易记录后触发。


事件来源：

Webhook Listener


消费者：

TradeContentAgent


payload 示例：

{
  "record_id": "a81c22f3",
  "received_at": "2026-05-01T03:12:22",
  "source": "manual",
  "safe_text": "今天关注半导体板块...",
  "is_publishable": true,
  "redacted_image_path": "data/webhook/redacted/..."
}


---

# 三、未来扩展事件

系统未来可能增加：

content.generated

内容生成完成


content.published

内容发布完成


engagement.updated

用户互动数据更新


strategy.optimized

策略更新


---

# 四、事件存储

所有事件都会写入 SQLite Event Log。

路径：

data/event_bus/events.sqlite


表结构：

id

event_type

payload_json

created_at


---

# 五、事件调试

可以通过 Event Monitor API 查看事件：

http://localhost:8010/events/recent


示例：

[
  {
    "id": 12,
    "event_type": "daily.picks.generated",
    "created_at": "2026-05-01T03:12:22",
    "payload": {...}
  }
]


---

# 六、事件设计建议

新增事件时建议：

事件名称清晰

payload 尽量稳定

避免频繁修改 schema
