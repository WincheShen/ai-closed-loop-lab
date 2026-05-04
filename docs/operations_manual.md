# AI Closed Loop Lab — 操作手册

本文档用于说明如何在本地启动、测试和日常运行 AI Closed Loop Lab 系统。

---

# 一、系统组件

本系统由以下几个核心服务组成：

1. TradingAgent Service

负责 AI 投研分析。

端口：8001


2. Webhook Listener

接收交易记录（文字 + 图片），并生成事件。

端口：8002


3. Event Monitor

查看系统事件日志。

端口：8010


4. Daily Workflow

执行每日选股流程。

脚本触发。

---

# 二、环境准备

进入项目目录：

cd ai-closed-loop-lab

创建虚拟环境：

python3.11 -m venv .venv

激活环境：

source .venv/bin/activate

安装依赖：

pip install -e ".[dev]"

---

# 三、启动系统

推荐使用统一启动脚本：

./scripts/start_all.sh

启动后系统会运行以下服务：

TradingAgent

Webhook Listener

Event Monitor


服务地址：

TradingAgent

http://localhost:8001/health


Webhook Listener

http://localhost:8002/health


Event Monitor

http://localhost:8010/events/recent


---

# 四、运行每日选股

触发 workflow：

python scripts/run_daily_workflow.py


执行流程：

Market Scan

→ Rule Engine

→ TradingAgent 分析

→ 生成 Daily Picks

→ 发送事件 daily.picks.generated


事件会被：

TopicGeneratorAgent

消费并生成内容任务。


---

# 五、查看系统事件

浏览器访问：

http://localhost:8010/events/recent


返回示例：

[
  {
    "id": 21,
    "event_type": "daily.picks.generated",
    "created_at": "2026-05-01T03:20:10",
    "payload": {
      "date": "2026-05-01",
      "num_candidates": 23
    }
  }
]


可用于调试：

系统是否产生事件

Agent 是否执行


---

# 六、测试交易记录

发送 webhook：

curl -X POST http://localhost:8002/webhook/trade \
  -F "text=今天关注半导体板块" \
  -F "source=manual"


系统会：

保存交易记录

→ 发布事件 trade.record.created

→ TradeContentAgent 生成内容任务


---

# 七、常见开发流程

开发时推荐流程：

1 启动系统

./scripts/start_all.sh


2 运行选股

python scripts/run_daily_workflow.py


3 查看事件

http://localhost:8010/events/recent


4 测试 webhook

curl webhook


---

# 八、系统事件说明

核心事件：


daily.picks.generated

每日选股完成


trade.record.created

接收到交易记录


未来可能扩展：

content.generated

content.published

engagement.updated


---

# 九、日志位置

系统事件日志：


data/event_bus/events.sqlite


交易记录：


data/webhook/trade_records.sqlite


每日选股结果：


data/daily_picks/


---

# 十、停止系统

如果使用 start_all.sh：

Ctrl + C


脚本会自动停止所有服务。

---

# 十一、故障排查

1 服务无法启动

检查端口是否占用：

lsof -i :8001


2 没有事件产生

检查：

Daily Workflow 是否运行


3 没有生成内容任务

检查 Event Monitor：

/events/recent


---

# 十二、系统架构简图


Market Data

→ Daily Workflow

→ EventBus

→ Agents

→ Content System



EventBus 同时写入：

SQLite Event Log


可通过 Event Monitor 查看。
