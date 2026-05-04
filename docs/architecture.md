# AI 闭环实验室 — 系统架构设计 (v0.2)

> 配套文档：[需求文档 requirements.md](./requirements.md)
>
> 本架构基于 2026-04-26 需求澄清重新组织，三大模块边界清晰、职责单一。

## 0. 新旧模块映射（Phase 3.5 → Phase 4 过渡状态）

Phase 4 引入了 `src/ai_platform/*` 作为"事件编排层"，但并未替换 Phase 1-3 的业务模块。
当前代码库中新旧模块**并行存在**，关系如下：

| 层次 | 模块路径 | 状态 | 职责 |
|------|---------|------|------|
| 业务 | `src/stock_analyzer/` | ✅ 保留（Phase 1） | 选股规则、热点识别、每日流水线 |
| 业务 | `src/trading_agent_service/` | ✅ 保留（Phase 1/3） | 单股深度分析 HTTP 服务 + Cache |
| 业务 | `src/webhook_listener/` | ✅ 保留（Phase 1） | 接收交易记录、文字/图片合规 |
| 业务 | `src/social_media_dispatcher/` | ✅ 保留（Phase 2） | TopicRouter + SmaClient 对 SMA 通信 |
| 编排 | `src/ai_platform/central_brain/` | 🆕 新增（Phase 4） | EventBus + Workflow Engine |
| 编排 | `src/ai_platform/content_ai/` | 🆕 新增（Phase 4） | TopicGeneratorAgent、TradeContentAgent |
| 编排 | `src/ai_platform/feedback_system/` | 🆕 新增（Phase 4） | StrategyFeedbackAgent + Analyzer |
| 编排 | `src/ai_platform/investment_ai/` | 📝 设计中 | 规划中，暂无实现 |

**依赖方向规则**（重要）：
- ✅ `ai_platform/*` **可以**导入 `stock_analyzer/*`、`social_media_dispatcher/*` 等业务模块
- ❌ 业务模块**不应**导入 `ai_platform/*`（保持业务层独立可测）
- ⚠️ 例外：`webhook_listener/server.py` 通过 `get_event_bus()` 发事件是允许的（入站适配器）

**启动入口的关系**：
| 用途 | 标准入口（推荐） | 旧入口（保留兼容） |
|------|----------------|----------------|
| TradingAgent HTTP 服务 | `scripts/run_trading_agent_service.py` | — |
| Webhook 接收器 | `scripts/run_webhook_listener.py` | — |
| 每日选股（直接） | `scripts/run_daily_scan.py` | — |
| 每日选股（事件驱动） | `scripts/run_daily_workflow.py` | — |
| 事件监控 API | `python -m ai_platform.cli event-monitor` | `scripts/run_event_monitor.py` |
| 策略反馈 API | `python -m ai_platform.cli strategy-metrics` | `scripts/run_strategy_metrics.py` |

**未来迁移方向**（不在 Phase 3.5 范围）：
- Phase 5 起，将 `stock_analyzer.pipelines.daily_scan` 的 HTTP 同步调用改为发事件给 `ai_platform` 编排
- `central_brain` 的分散 SQLite（cache.sqlite / events.sqlite / strategy_metrics.sqlite）统一为单一 `central_brain.db`

---

## 1. 顶层架构

```
                  ┌────────────────────────────────────────┐
                  │   外部输入                              │
                  │   • AKShare 行情                        │
                  │   • 沈经理 wechat/飞书 webhook（交易记录）│
                  │   • 小红书爆款文章                       │
                  └─────────────────┬──────────────────────┘
                                    │
        ┌───────────────────────────┴───────────────────────────┐
        │                                                       │
        ▼                                                       ▼
┌──────────────────────┐                         ┌──────────────────────────┐
│  Webhook 接收器       │                         │  定时调度器                │
│  • IM 消息路由        │                         │  • 每日 15:35 选股        │
│  • 图片/文字解析      │                         │  • 每周日 20:00 复盘      │
└──────────┬───────────┘                         └──────────┬───────────────┘
           │                                                │
           ▼                                                ▼
┌────────────────────────────────────────────────────────────────────────┐
│                     元数据中心 Central Brain                            │
│   SQLite + 向量存储 + EventBus + 选股规则版本库                          │
└────────────────────────────────────────────────────────────────────────┘
                                    │
        ┌───────────────────────────┼───────────────────────────┐
        ▼                           ▼                           ▼
┌──────────────────┐    ┌────────────────────┐    ┌──────────────────────┐
│ 模块1            │───▶│ 模块3              │    │ 模块2                 │
│ 股票分析平台      │    │ TradingAgent 服务  │◀───│ Social Media          │
│ Stock Analyzer   │    │ HTTP API + Cache   │    │ Automation            │
│                  │    │ + 知识星球同步     │    │                       │
│ • 选股规则引擎    │    └────────────────────┘    │ • 主题研究            │
│ • 热点识别        │                              │ • 合规创作            │
│ • 交易员推荐      │           ▲                  │ • 引流评论            │
│ • 复盘归因        │           │                  │ • 评论回复            │
└──────────────────┘           │                  │ • 运营数据复盘        │
        │                      │                  └──────────────────────┘
        │                      │                            │
        │ 推荐清单+板块         │ 单股深度分析                │ 评论高质量观点反哺
        └──────────────────────┴────────────────────────────┘
```

## 2. 三大模块详细设计

### 2.1 模块一：股票分析平台 (Stock Analyzer)

```
src/stock_analyzer/
├── data_source/
│   ├── akshare_client.py         # 全市场数据拉取
│   ├── hot_sector_detector.py    # 热点板块识别
│   └── stock_filter.py           # 排除ST/新股等
├── rules/
│   ├── rule_registry.py          # 规则注册表（版本化）
│   ├── builtin/                  # 内置规则
│   │   ├── volume_price.py       # 量价规则
│   │   ├── ma_breakout.py        # 均线突破
│   │   └── fund_flow.py          # 资金流向
│   └── rule_engine.py            # 规则匹配引擎
├── trader_recommender/
│   ├── aggressive.py             # 爆发力推荐器
│   └── stable.py                 # 稳健推荐器
├── post_mortem/
│   ├── trade_ingestor.py         # 接收沈经理交易记录
│   ├── attribution.py            # 归因分析
│   └── rule_evolver.py           # 规则参数优化建议
└── pipelines/
    ├── daily_scan.py             # 每日选股流水线
    └── weekly_review.py          # 每周复盘流水线
```

**关键流程**：
```
15:35 触发 → AKShare 拉数 → 热点板块Top5 → 规则引擎筛选
       → 调用 TradingAgent /analyze（带cache）→ 交易员综合
       → 输出"激进推荐 1-3只" + "稳健推荐 1-3只"
       → 写入 Central Brain → 推送给 Social Media（topic 输入）
```

### 2.2 模块二：Social Media Automation

> 沿用现有 `Social-media-automation/` 项目作为基础，扩展四个新子模块。

```
src/social_media/
├── topic_router/                 # ⭐ 新增：选题路由（不主动搜热点）
│   └── from_stock_analyzer.py    # 输入只来自模块1
├── trade_ingestor/               # ⭐ 新增：交易信息合规处理
│   ├── webhook_listener.py       # wechat/飞书 webhook
│   ├── image_redactor.py         # 图片脱敏（模糊代码/名称）
│   └── text_compliance.py        # 文字合规改写
├── content_research/
│   └── xhs_trend_analyzer.py     # 小红书最近1-2天爆款研究
├── creative_engine/              # 已有：创作引擎
│   ├── primary_creator.py        # 数据驱动创作
│   └── secondary_editor.py       # ⭐ 新增：人工选题二次加工
├── traffic_growth/               # ⭐ 新增：引流互动
│   ├── hot_article_finder.py     # 找当日最热文章
│   └── targeted_commenter.py     # 针对性评论
├── comment_replier/              # ⭐ 新增：评论回复
│   ├── comment_classifier.py     # 评论分级
│   └── auto_reply.py             # 自动回复
├── analytics/                    # ⭐ 新增：运营复盘
│   ├── daily_collector.py        # 每日数据收集
│   └── weekly_optimizer.py       # 每周策略调整
└── publisher/                    # 已有：xhs_cli 发布层
```

**关键约束**：
- `topic_router` 是**唯一**选题入口，确保不主动搜热点
- `trade_ingestor` 必须在内容生成前完成脱敏，未脱敏数据**不进入**创作引擎

### 2.3 模块三：TradingAgent 服务

> 重构 `tradingAgents_neo/` 从单次脚本变为 HTTP 服务。

```
src/trading_agent_service/
├── api/
│   ├── server.py                 # FastAPI 入口
│   ├── routes/
│   │   ├── analyze.py            # POST /analyze
│   │   ├── report.py             # GET /report/{symbol}
│   │   └── stats.py              # GET /stats, /health
│   └── middlewares/
│       └── rate_limit.py         # API 限流
├── cache/
│   ├── cache_manager.py          # 缓存读写
│   ├── invalidation.py           # 失效判定（价格区间/时间/公告）
│   └── price_watcher.py          # 监控触发再评估价格
├── analysis/
│   └── tradingagents_adapter.py  # 调用 tradingagents_neo 包
├── knowledge_planet/             # ⭐ 新增：知识星球同步
│   ├── publisher.py              # 发布报告
│   └── formatter.py              # Markdown→星球格式
└── models/
    └── report.py                 # Report Pydantic schema
```

**API 契约**：

```python
POST /analyze
Request:
{
  "symbol": "600519",
  "force_refresh": false,        # 强制跳过 cache
  "depth": "deep" | "quick"
}

Response:
{
  "symbol": "600519",
  "name": "贵州茅台",
  "report": {
    "summary": "...",
    "technical": {...},
    "fundamental": {...},
    "bull_case": "...",
    "bear_case": "...",
    "final_decision": "BUY|HOLD|SELL",
    "reevaluation_price_range": [1480, 1620],   # ⭐ 触发再评估的价格区间
    "valid_until": "2026-04-29"
  },
  "metadata": {
    "evaluated_at": "2026-04-26T16:00:00",
    "cache_hit": false,
    "knowledge_planet_url": "https://t.zsxq.com/..."
  }
}
```

## 3. 跨模块通信

### 3.1 同步调用（HTTP）

| 调用方 | 被调方 | API |
|--------|--------|-----|
| Stock Analyzer | TradingAgent Service | `POST /analyze` |
| Social Media (creative) | TradingAgent Service | `GET /report/{symbol}` |
| External (沈经理临时查询) | TradingAgent Service | `POST /analyze` |

### 3.2 异步事件（EventBus）

| 事件 | 发布方 | 订阅方 |
|------|--------|--------|
| `daily_picks_ready` | Stock Analyzer | Social Media |
| `trade_record_received` | Webhook | Stock Analyzer (复盘) + Social Media (合规处理) |
| `post_published` | Social Media | Central Brain |
| `comment_insight_extracted` | Social Media | Stock Analyzer |
| `report_generated` | TradingAgent | Knowledge Planet Publisher |

## 4. 数据持久化

| 数据 | 存储 | 表/集合 |
|------|------|---------|
| 选股规则（版本化） | SQLite | `rules`, `rule_versions` |
| 每日推荐结果 | SQLite | `daily_picks` |
| 交易记录（沈经理实盘） | SQLite | `trade_records` |
| 复盘归因 | SQLite | `attributions` |
| TradingAgent 报告 | SQLite + 文件 | `reports`（元数据）+ `reports/*.json` |
| 报告价格触发监控 | SQLite | `price_watchers` |
| 社媒发布历史 | SQLite | `posts` |
| 引流评论记录 | SQLite | `outbound_comments` |
| 评论回复记录 | SQLite | `replies` |
| 运营指标 | SQLite | `analytics_daily` |
| Agent 间事件流 | SQLite | `events` |
| 向量记忆 | sqlite-vec | `memories` |

## 5. 部署拓扑

```
┌──────────────────────────────────────────┐
│  本地单机部署 (Mac mini / 工作站)          │
│                                          │
│  ┌────────────────────────────────────┐  │
│  │ TradingAgent Service               │  │
│  │ uvicorn :8001 (常驻)                │  │
│  └────────────────────────────────────┘  │
│                                          │
│  ┌────────────────────────────────────┐  │
│  │ Webhook Listener                   │  │
│  │ uvicorn :8002 (常驻)                │  │
│  └────────────────────────────────────┘  │
│                                          │
│  ┌────────────────────────────────────┐  │
│  │ Scheduler                          │  │
│  │ 每日触发 Stock Analyzer             │  │
│  │ 每日触发 Social Media (受控)        │  │
│  │ 每周触发 复盘                       │  │
│  └────────────────────────────────────┘  │
│                                          │
│  ┌────────────────────────────────────┐  │
│  │ Chrome (CDP) × N profiles          │  │
│  │ for xiaohongshu-skills             │  │
│  └────────────────────────────────────┘  │
│                                          │
│  ┌────────────────────────────────────┐  │
│  │ Central Brain (SQLite + files)     │  │
│  └────────────────────────────────────┘  │
└──────────────────────────────────────────┘
```

**关键决策**：
- TradingAgent 服务**单独运行**（独立 LLM 配额、独立 Cache、独立日志）
- 不上云、不容器化对外暴露（数据隔离要求）

## 6. 演进路线图（基于 P0/P1/P2）

### Phase 1（Week 1-2）— P0 基础能力
- [ ] TradingAgent 服务化（含 Cache）
- [ ] 选股规则引擎 + YAML 规则文件
- [ ] 每日选股流水线（mock 推荐输出）
- [ ] Webhook 接收器 + 图片脱敏

### Phase 2（Week 3-4）— P0 闭环打通
- [ ] Stock Analyzer ↔ TradingAgent 集成
- [ ] Social Media 选题路由（topic_router）
- [ ] 创作引擎接入合规处理后的交易信息
- [ ] 交易员综合推荐器

### Phase 3（Week 5-6）— P1 知识反哺
- [ ] 知识星球同步（先 PoC）
- [ ] 交易结果复盘 + 规则优化建议
- [ ] 引流评论模块（小流量灰度）

### Phase 4（Week 7+）— P2 自治进化
- [ ] 自动回复评论
- [ ] 运营数据 → 内容策略自我优化
- [ ] 评论价值反哺到选股
- [ ] （可选）实盘小额灰度

## 7. 与原 v0.1 的差异

| v0.1（旧） | v0.2（新） | 原因 |
|-----------|-----------|------|
| Explorer + Strategist + Executioner + Influencer 四 Agent | Stock Analyzer + TradingAgent Service + Social Media 三模块 | 沈经理需求更聚焦，无需独立 Executioner（实盘暂不接） |
| TradingAgent 作为 Strategist 的子组件 | 独立 HTTP 服务 + Cache + 知识星球 | 复用率高，需要独立部署 |
| Influencer 主动选题 | Social Media 严格不主动搜热点 | 风控合规要求 |
| 无 Webhook 入口 | 新增 wechat/飞书 接收器 | 沈经理人工交易需要被系统感知 |
| 强化学习风格的 Prompt 进化 | 显式的"选股规则版本化 + 归因优化" | 更可解释、可控 |
