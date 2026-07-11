# AI 闭环实验室 — 项目状态评估

> 更新日期：2026-06-14
> 状态：NAS 部署运行中

---

## 0. 整体架构

```
┌─────────────────────────────────────────────────────────────────────┐
│                         前端 Dashboard (React + Vite)                 │
│  AgentDailyReport │ Portfolio │ Personas │ Strategy │ QuantMonitor   │
│  ContentPipeline  │ Records   │ Analyze  │ StrategyEvolution        │
└───────────────────────────────┬─────────────────────────────────────┘
                                │ HTTP API (FastAPI :8002)
┌───────────────────────────────┴─────────────────────────────────────┐
│                      Webhook Listener / Web Server                    │
│   • 交易记录接收  • Agent Report API  • Personas API  • 合规处理      │
└───────────────────────────────┬─────────────────────────────────────┘
                                │
┌───────────────────────────────┴─────────────────────────────────────┐
│              LangGraph Workflow (每日交易决策管线)                      │
│  MarketBrain → Explorer → Strategist → RiskGovernor → Executioner    │
│                                                        → Influencer  │
└───────────────────────────────┬─────────────────────────────────────┘
                                │
        ┌───────────────────────┼───────────────────────┐
        ▼                       ▼                       ▼
┌──────────────────┐  ┌──────────────────┐  ┌──────────────────────┐
│ Central Brain    │  │ TradingAgent     │  │ 定时调度器             │
│ • MemoryStore    │  │ Service (:8001)  │  │ • 09:35 MarketBrain  │
│ • EventBus      │  │ • 深度分析 API   │  │ • 盘中持仓复审        │
│ • SQLite 持久化  │  │ • Cache 层       │  │ • 15:05 收盘分析      │
│ (central_brain   │  │ • tradingAgents  │  │ • 15:35 完整管线      │
│    .db)          │  │   _neo 集成      │  │ • 15:40 自选股池检查  │
└──────────────────┘  └──────────────────┘  │ • 周日 20:00 复盘    │
                                            └──────────────────────┘
```

### 模块路径映射

| 层次 | 模块路径 | 职责 |
|------|---------|------|
| 业务 | `src/stock_analyzer/` | 选股规则、热点识别、每日流水线 |
| 业务 | `src/trading_agent_service/` | 单股深度分析 HTTP 服务 + Cache |
| 业务 | `src/webhook_listener/` | 接收交易记录、Web API、合规 |
| 业务 | `src/social_media_dispatcher/` | TopicRouter + SmaClient |
| 业务 | `src/strategy_mining/` | 公众号文章 → 策略提取 |
| Agent | `src/agents/cio/` | MarketBrain + TradingPersona（多人格） |
| Agent | `src/agents/explorer/` | 市场扫描 + 候选票筛选 |
| Agent | `src/agents/strategist/` | LLM 信号生成 |
| Agent | `src/agents/risk/` | 风控裁决 |
| Agent | `src/agents/executioner/` | 执行（mock/paper） |
| Agent | `src/agents/influencer/` | 内容引擎 → SMA 派发 |
| Agent | `src/agents/reviewer/` | 盘中复审 + 收盘分析 |
| Agent | `src/agents/memory/` | 交易归因 |
| 编排 | `src/graph/` | LangGraph 工作流 |
| 编排 | `src/ai_platform/` | EventBus + Workflow Engine |
| 闭环 | `src/feedback_loop/` | 回测引擎 + Prompt 进化 |
| 基础 | `src/infra/` | Config + Logger + LLM Adapter |
| 基础 | `src/central_brain/` | MemoryStore (SQLite) |

---

## 1. 需求文档 (requirements.md) 功能点完成度

### 模块一：股票分析平台 — 完成度 ~85%

| 功能需求 | 状态 | 实现位置 | 备注 |
|---------|------|---------|------|
| FR-1.1 每日选股流水线 | ✅ 完成 | `src/stock_analyzer/pipelines/daily_scan.py` | AKShare → 热点 → 规则 → TradingAgent |
| FR-1.2 选股规则管理 | ✅ 完成 | `src/stock_analyzer/rules/` + `config/rules.yaml` | YAML + 权重 + 版本化 |
| FR-1.3 交易结果复盘 | ⚠️ 部分 | `src/agents/memory/trade_attribution.py` + `src/feedback_loop/` | 归因已实现，周报自动生成需更多运营数据 |
| FR-1.4 交易员推荐逻辑 | ✅ 完成 | `src/agents/strategist/signal_generator.py` | aggressive/stable/candidate 分桶 |

### 模块二：Social Media Automation — 完成度 ~35%

| 功能需求 | 状态 | 实现位置 | 备注 |
|---------|------|---------|------|
| FR-2.1 数据驱动选题 | ✅ 完成 | `src/social_media_dispatcher/topic_router.py` | 从选股结果路由 |
| FR-2.2 主题相关内容研究 | ⏳ 未实现 | — | 依赖 SMA 项目的爆款研究功能 |
| FR-2.3 交易信息合规处理 | ✅ 完成 | `src/webhook_listener/image_redactor.py` + `text_compliance.py` | 图片脱敏 + 敏感词替换 |
| FR-2.4 内容创作引擎 | ⚠️ 部分 | `src/agents/influencer/content_engine.py` | 基础模板已有，深度创作依赖 SMA |
| FR-2.5 二次加工模式 | ⏳ 未实现 | — | 需要人工选题入口 |
| FR-2.6 引流模块（评论） | ⏳ 未实现 | — | SMA 项目侧能力就绪，编排未接入 |
| FR-2.7 自动回复评论 | ⏳ 未实现 | — | |
| FR-2.8 运营数据收集 | ⏳ 部分 | `scripts/sync_sma_engagements.py` | 同步脚本有，自动优化未实现 |
| FR-2.9 评论价值反哺 | ⏳ 未实现 | — | |

### 模块三：TradingAgent 服务 — 完成度 ~80%

| 功能需求 | 状态 | 实现位置 | 备注 |
|---------|------|---------|------|
| FR-3.1 服务化 | ✅ 完成 | `src/trading_agent_service/api/` | FastAPI + /analyze + /report + /health |
| FR-3.2 缓存层 | ✅ 完成 | `src/trading_agent_service/cache/` | SQLite + (symbol, date) 维度 |
| FR-3.3 知识星球同步 | ⏳ 未实现 | — | 需调研 API 可行性 |
| FR-3.4 报告内容规范 | ✅ 完成 | `src/trading_agent_service/analysis/` | 多空辩论 + 评估区间 |

---

## 2. Phase 进度对照

### Phase 1 — 基础管线 ✅ 完成

- [x] TradingAgent 服务化 + Cache
- [x] 选股规则引擎 + 每日流水线
- [x] Webhook 接收 + 合规处理
- [x] Docker 容器化 + NAS 部署

### Phase 2 — Social Media 对接 ✅ 基本完成

- [x] TopicRouter 选题路由
- [x] SmaClient 对接 Social-media-automation
- [x] 合规处理（图片脱敏 + 文字）
- [ ] 引流评论模块
- [ ] 运营数据自我优化

### Phase 3 — Cognitive Agent ✅ 完成

- [x] TradingPersona 加载 + 多人格支持（默认/段永平/巴菲特）
- [x] MarketBrain 量化 + LLM 综合判定
- [x] LangGraph 6 节点管线
- [x] RiskGovernor approve/reduce/reject
- [x] TradingAgents_neo 集成（真实深度分析）
- [x] 资金账户管理（每人格独立账户）

### Phase 3.5 — 可观测性 ✅ 大部分完成

- [x] LLM 调用打点（MemoryStore.record_llm_call）
- [x] daily_picks 归档
- [x] social_posts 归档
- [x] Streamlit Dashboard (scripts/dashboard.py)
- [ ] SMA 同步器完整实现
- [ ] LLM pricing YAML 落盘

### Phase 4 — AI Platform 编排 ⚠️ 部分完成

- [x] EventBus 框架
- [x] Workflow Engine (daily_market_workflow)
- [x] Content AI (TopicGeneratorAgent, TradeContentAgent)
- [x] StrategyFeedbackAgent + Analyzer
- [ ] Investment AI（设计中）

---

## 3. 前端 Dashboard 页面

| 页面 | 文件 | 功能 | 状态 |
|------|------|------|------|
| Agent 日报 | `AgentDailyReport.tsx` | 市场判断 + 选股结果 + 交易信号 | ✅ |
| 投资组合 | `Portfolio.tsx` | 持仓 + 盈亏 + 资金 | ✅ |
| 人格管理 | `Personas.tsx` | 多人格列表 + 账户信息 | ✅ |
| 策略管理 | `Strategy.tsx` | 选股规则配置 | ✅ |
| 量化监控 | `QuantMonitor.tsx` | 量化指标 + 回测 | ✅ |
| 内容管线 | `ContentPipeline.tsx` | SMA 任务状态 | ✅ |
| 交易记录 | `Records.tsx` | Webhook 交易流水 | ✅ |
| 策略进化 | `StrategyEvolution.tsx` | 策略版本 + 归因 | ✅ |
| 分析面板 | `Analyze.tsx` | 个股深度分析 | ✅ |
| Agent 工作区 | `AgentWorkspace.tsx` | Agent 事件流 | ✅ |

---

## 4. 部署与运维

| 组件 | 状态 | 说明 |
|------|------|------|
| Docker 镜像构建 | ✅ | 前后端打包成单一镜像 |
| NAS 部署 | ✅ | docker-compose + volumes |
| 定时调度器 | ✅ | scheduler.py (schedule 库) |
| 健康检查 | ✅ | 每日 16:00 自动 |
| LLM 配置 | ✅ | Azure OpenAI via openai provider |
| 数据持久化 | ✅ | /app/data/central_brain.db |

---

## 5. 总结评分

| 维度 | 完成度 | 说明 |
|------|--------|------|
| 核心投资决策闭环 | **~90%** | 选股→分析→风控→执行 全链路打通 |
| 可观测性 | **~80%** | 数据落地 + Dashboard + LLM 打点 |
| Social Media 自动化 | **~35%** | 对接+创作基础有，引流/回复/优化未做 |
| 反馈进化 | **~60%** | 归因+回测框架有，策略自动调参未完整闭合 |
| 部署运维 | **~90%** | Docker + NAS + Scheduler 稳定运行 |
| **整体功能完成度** | **~70%** | |

---

## 6. 待完成事项（按优先级）

### P0 — 运行稳定性

- [ ] 确保 scheduler 每日自动执行（当前手动触发居多）
- [ ] MarketBrain 数据库保存验证（DB_PATH 路径已统一为 central_brain.db）
- [ ] akshare 数据源网络稳定性（NAS 上 eastmoney 被封，需依赖 Sina fallback）

### P1 — 反馈闭环

- [ ] 平仓时自动写归因 (trade_attribution)
- [ ] Strategist 读取历史 lesson
- [ ] 周复盘自动生成《策略优化建议》
- [ ] 策略权重自动调整

### P2 — Social Media 深度功能

- [ ] 引流评论模块接入
- [ ] 自动回复评论
- [ ] 运营数据收集 + 自我优化
- [ ] 评论价值反哺选股

### P3 — 增值功能

- [ ] 知识星球自动同步
- [ ] 二次加工模式（人工选题入口）
- [ ] Investment AI 模块

---

## 7. 已知技术债

1. **两套管线并存** — `DailyScanPipeline` (老) 与 LangGraph pipeline (新) 共存
2. **数据库分散** — cache.sqlite / events.sqlite / trade_records.sqlite / central_brain.db 未完全统一
3. **热点板块瘸腿** — NAS 上 eastmoney 被封，需走 Sina + Emappdata fallback
4. **Influencer 模板化** — 内容创作深度不够，需 SMA 项目配合
5. **测试覆盖** — 端到端测试覆盖不完整
