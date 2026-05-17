# AI Closed Loop Lab — Cognitive Agent 设计文档

> 目标：将当前“固定时间触发的自动化交易流水线”演进为一个具备持续观察、独立判断、策略演进、风险治理、复盘学习和社交分享能力的 Cognitive Agent。
>
> 版本：v0.1
>
> 状态：设计稿

## 1. 背景与目标

当前系统已经具备基础闭环：

```text
定时调度 → 市场扫描 → LLM 分析 → 虚拟交易 → 持仓复审 → 收盘总结 → 社交内容
```

但当前架构仍然偏“流水线自动化”：

- 由固定时间表驱动，而不是由市场状态和目标驱动。
- 策略主要由静态规则和 prompt 决定，而不是持续自我演进。
- 有交易记录和复盘记录，但尚未形成稳定的长期学习机制。
- 社交内容是交易结果的下游输出，还没有成为策略反馈和市场叙事感知的一部分。

本设计目标是演进为：

```text
Cognitive Agent = 自主投资研究者 + 组合经理 + 风控官 + 复盘教练 + 社交分享者
```

它应该能够：

1. 每天主动理解市场环境。
2. 根据自己的炒股风格制定当天作战计划。
3. 自主选择候选股、生成交易决策、管理仓位。
4. 在交易后进行结构化归因和策略修正。
5. 持续提出、验证、淘汰或晋级交易策略。
6. 坚持把自己的市场观察、交易思考和复盘沉淀分享到社交圈。

## 2. Success Metrics

### 2.1 核心业务指标

| 指标 | 目标 | 口径 |
| --- | --- | --- |
| 炒股胜率 | `> 60%` | 已关闭交易中，收益率 `> 0` 的交易笔数 / 已关闭交易总笔数 |
| 社交圈日浏览量 | `> 5000 / day` | 每日发布内容在目标平台的总浏览量 |

### 2.2 辅助交易指标

胜率本身不足以衡量交易质量，需要同时跟踪：

| 指标 | 建议目标 | 说明 |
| --- | --- | --- |
| 平均盈亏比 | `> 1.2` | 平均盈利金额 / 平均亏损金额 |
| 最大回撤 | `< 15%`（纸面盘阶段） | 组合净值最大回撤 |
| Profit Factor | `> 1.3` | 总盈利 / 总亏损 |
| 单票最大亏损 | `< 5%-8%` | 根据策略风格配置 |
| 策略有效样本数 | 每个策略 `>= 30` 笔 | 避免小样本误判 |
| 空仓质量 | 记录“不交易日”的合理性 | 判断系统是否会主动防守 |

### 2.3 辅助内容指标

| 指标 | 建议目标 | 说明 |
| --- | --- | --- |
| 日更完成率 | `> 90%` | 每个交易日是否发布至少一条内容 |
| 平均浏览量 | `> 5000` | 滚动 7 日均值 |
| 收藏/点赞率 | 持续提升 | 衡量内容质量 |
| 评论有效观点数 | 每周增长 | 可反哺市场叙事判断 |
| 内容-交易一致性 | 高 | 分享内容应来自真实研究和复盘，不应脱离系统观点 |

### 2.4 重要约束

`胜率 > 60%` 和 `日浏览量 > 5000` 是系统优化目标，不应被当作保证结果。系统必须同时优化风险、可解释性和长期可持续性，避免为了短期指标采取过度交易、标题党、追热点或过度拟合策略。

## 3. 设计原则

1. **目标驱动，而非流程驱动**
   - 固定时间任务只是事件源。
   - 真正的决策由 Cognitive Agent 根据市场、持仓、历史表现和当前目标生成。

2. **先市场后个股**
   - 先判断市场 regime、风险偏好和今日作战姿态。
   - 再决定是否选股、选什么风格、用多少仓位。

3. **策略人格稳定，参数持续演进**
   - Agent 应有稳定交易风格。
   - 可以调整权重、参数和禁区，但不能每天随机换风格。

4. **风控独立于交易冲动**
   - Strategist 可以提出买卖建议。
   - RiskGovernor 必须拥有 veto 权。

5. **每个决策都必须可复盘**
   - 买入、卖出、空仓、降仓、内容发布都要记录原因和证据。

6. **学习必须结构化**
   - 复盘不是生成一段总结，而是产出可被下次决策读取的 lesson、参数调整和策略状态变化。

7. **内容分享来自真实研究**
   - 社交内容是系统观点的外显，不是独立的营销脚本。

## 4. 目标架构总览

```text
┌───────────────────────────────────────────────────────────────┐
│                    Cognitive Agent Kernel                     │
│  Observe → Think → Plan → Act → Evaluate → Remember → Share    │
└──────────────────────────────┬────────────────────────────────┘
                               │
        ┌──────────────────────┼──────────────────────┐
        ▼                      ▼                      ▼
┌─────────────────┐   ┌─────────────────┐   ┌─────────────────┐
│ Market Brain    │   │ Strategy Brain  │   │ Social Brain    │
│ 市场世界模型     │   │ 策略研究与演进   │   │ 社交表达与反馈   │
└────────┬────────┘   └────────┬────────┘   └────────┬────────┘
         │                     │                     │
         ▼                     ▼                     ▼
┌─────────────────┐   ┌─────────────────┐   ┌─────────────────┐
│ Explorer         │   │ Strategist      │   │ Influencer      │
│ 候选股发现       │   │ 个股决策         │   │ 内容生成/发布    │
└────────┬────────┘   └────────┬────────┘   └────────┬────────┘
         │                     │                     │
         └──────────────┬──────┴──────────────┬──────┘
                        ▼                     ▼
              ┌─────────────────┐   ┌─────────────────┐
              │ RiskGovernor    │   │ Memory System   │
              │ 风控与组合治理   │   │ 记忆/归因/学习   │
              └────────┬────────┘   └────────┬────────┘
                       ▼                     │
              ┌─────────────────┐            │
              │ Executioner     │◀───────────┘
              │ 执行/持仓管理    │
              └─────────────────┘
```

## 5. Agent Kernel

### 5.1 职责

Agent Kernel 是系统主脑，负责把固定任务、市场事件、持仓事件、用户指令和社交反馈统一转化为任务计划。

它不直接做所有事情，而是负责：

- 接收事件。
- 判断优先级。
- 选择要调用的子 Agent。
- 维护当天计划。
- 追踪任务完成情况。
- 在关键节点生成反思和记忆。

### 5.2 主循环

```text
Observe:
  读取市场数据、持仓状态、历史表现、社交反馈、新闻/叙事信号

Think:
  判断市场状态、系统状态、当前风险和机会

Plan:
  生成今日作战计划和任务队列

Act:
  调用 Explorer / Strategist / RiskGovernor / Executioner / Influencer

Evaluate:
  评估动作结果，更新交易和内容指标

Remember:
  写入结构化记忆、经验、策略调整建议

Share:
  生成面向社交圈的观点、复盘或观察
```

### 5.3 事件类型

| 事件 | 示例 | 处理方式 |
| --- | --- | --- |
| `market.pre_open` | 盘前 | 生成今日计划 |
| `market.open` | 开盘 | 检查持仓风险 |
| `market.tick` | 每 5-30 分钟 | 检查异常波动和持仓触发条件 |
| `market.close` | 收盘 | 复盘、更新策略表现、生成内容 |
| `position.stop_loss_hit` | 触发止损 | RiskGovernor 立即评估 |
| `strategy.underperforming` | 策略失效 | 降权或进入观察 |
| `social.post_metrics_ready` | 内容数据回收 | 更新内容策略 |
| `user.command` | 用户指令 | 可插队任务 |

## 6. Trading Persona

### 6.1 目标

Trading Persona 定义 Agent 的长期交易风格和行为边界。它类似“投资人格”，用于约束每日决策，避免 LLM 因上下文变化而风格漂移。

### 6.2 示例配置

```yaml
persona:
  id: short_term_hot_rotation_v1
  name: 短线热点轮动专家
  philosophy:
    - 先判断市场赚钱效应，再决定是否进攻。
    - 只做有成交额、有板块逻辑、有技术确认的机会。
    - 宁可错过，不在弱势市场追高。
  preferred_holding_days: [1, 5]
  preferred_setups:
    - 热点板块前排回踩
    - 放量突破后第一次确认
    - 强趋势中的缩量调整
  avoid_setups:
    - ST 和流动性差股票
    - 高位连续加速后的无量追涨
    - 市场弱势时的纯题材接力
  risk_limits:
    max_single_position_pct: 0.10
    max_total_position_pct:
      bull: 0.70
      neutral: 0.40
      bear: 0.15
    default_stop_loss_pct: 0.05
  social_style:
    tone: 理性、克制、复盘型
    avoid:
      - 夸张收益承诺
      - 无依据喊单
      - 过度煽动情绪
```

### 6.3 演进边界

Agent 可以调整：

- 策略权重。
- 选股阈值。
- 仓位上限。
- 不同 market regime 下启用/禁用哪些策略。
- 内容选题风格。

Agent 不应自动改变：

- 交易品类边界。
- 最大风险上限。
- 是否进入实盘。
- 是否绕过风控。
- 是否发布不合规内容。

## 7. Market Brain

### 7.1 职责

Market Brain 负责形成市场世界模型：

- 大盘趋势。
- 市场强弱。
- 成交量结构。
- 热点板块。
- 赚钱效应。
- 亏钱效应。
- 风格偏好。
- 风险偏好。
- 今日适合进攻、防守还是观察。

### 7.2 输出

```json
{
  "trade_date": "2026-05-14",
  "market_regime": "neutral",
  "risk_appetite": "medium",
  "dominant_styles": ["热点轮动", "大成交趋势股"],
  "hot_sectors": ["银行", "电力", "AI应用"],
  "avoid_styles": ["高位缩量追涨", "微盘题材接力"],
  "recommended_posture": "selective_attack",
  "max_total_position_pct": 0.40,
  "strategy_bias": {
    "hot_sector_pullback": 0.35,
    "volume_breakout": 0.25,
    "defensive_bluechip": 0.20,
    "mean_reversion": 0.20
  },
  "daily_questions": [
    "AI应用方向是否重新放量？",
    "高股息是否继续吸收防守资金？",
    "指数是否站稳关键均线？"
  ]
}
```

### 7.3 关键能力

1. `MarketRegimeDetector`
   - 判断 bull / neutral / bear / panic / rebound。

2. `HotSectorAnalyzer`
   - 结合涨幅、成交额、资金流、持续性和龙头表现。

3. `RiskAppetiteAnalyzer`
   - 涨跌停家数、连板高度、亏钱效应、指数结构。

4. `NarrativeTracker`
   - 跟踪政策、新闻、社交讨论和内容反馈中的主题变化。

## 8. Explorer

Explorer 从“每天固定扫 Top N”升级为“根据 Market Brain 指令选择扫描方式”。

### 8.1 输入

- Trading Persona。
- Market Brain 输出。
- 当前持仓。
- 最近策略表现。
- 数据源可用性。

### 8.2 输出

候选股不只是股票列表，而是带证据的研究对象：

```json
{
  "symbol": "600519",
  "name": "贵州茅台",
  "candidate_type": "defensive_bluechip",
  "matched_strategies": ["defensive_pullback_v1"],
  "evidence": {
    "sector": "白酒",
    "liquidity": "high",
    "trend": "sideways",
    "valuation": "reasonable",
    "volume_signal": "normal"
  },
  "risks": [
    "板块持续性不足",
    "大盘风险偏好较低"
  ]
}
```

## 9. Strategist

Strategist 从“逐个候选股问 LLM 是否 BUY”升级为“带历史记忆和策略上下文的交易决策者”。

### 9.1 输入

- 候选股数据。
- Market Brain 输出。
- Trading Persona。
- 相关历史交易记忆。
- 当前持仓和资金状态。
- 策略表现统计。

### 9.2 输出

```json
{
  "symbol": "xxxxxx",
  "action": "buy|sell|hold|watch",
  "strategy_id": "hot_sector_pullback_v1",
  "entry_price": 12.30,
  "target_price": 13.50,
  "stop_loss": 11.70,
  "position_pct": 0.06,
  "confidence": 0.72,
  "expected_holding_days": 3,
  "thesis": "买入逻辑",
  "invalidation": "什么情况说明 thesis 失效",
  "similar_past_cases": [
    {
      "trade_id": "T20260501-001",
      "similarity": 0.82,
      "outcome": "loss",
      "lesson": "弱势市场突破失败率高"
    }
  ]
}
```

### 9.3 重要变化

当前 Strategist 的问题是每次分析相对独立。目标版本必须做到：

- 检索类似历史交易。
- 检查当前策略近期胜率。
- 判断是否与当前 market regime 匹配。
- 生成明确的 thesis 和 invalidation。
- 可以给出 `watch`，而不是非买即跳过。

## 10. RiskGovernor

RiskGovernor 是独立风控官，必须在 Executioner 前执行。

### 10.1 职责

- 检查总仓位。
- 检查单票仓位。
- 检查板块集中度。
- 检查策略集中度。
- 检查连续亏损。
- 检查 market regime 是否允许该策略。
- 检查是否触发停手机制。
- 对交易建议 approve / reduce / reject。

### 10.2 输出

```json
{
  "decision": "approve|reduce|reject",
  "original_position_pct": 0.10,
  "approved_position_pct": 0.05,
  "reason": "当前市场为 neutral，且同板块已有持仓，仓位减半",
  "risk_flags": [
    "sector_concentration",
    "recent_strategy_drawdown"
  ]
}
```

### 10.3 Veto 规则示例

| 场景 | 动作 |
| --- | --- |
| 总仓位超过 Market Brain 上限 | reject |
| 单策略连续 3 笔亏损 | reduce 或 reject |
| 当前 market regime 禁用该策略 | reject |
| 单票风险收益比低于 1.2 | reject |
| 同板块仓位超过上限 | reduce |
| 触发日内最大亏损 | reject all new buys |

## 11. Executioner / Portfolio Manager

Executioner 负责订单和持仓，Portfolio Manager 负责组合层面的状态。

### 11.1 组合状态

```json
{
  "cash": 800000,
  "market_value": 200000,
  "total_equity": 1000000,
  "total_position_pct": 0.20,
  "positions": [],
  "sector_exposure": {
    "AI应用": 0.08,
    "银行": 0.06
  },
  "strategy_exposure": {
    "hot_sector_pullback_v1": 0.10
  },
  "daily_pnl_pct": -0.4,
  "max_drawdown_pct": 3.2
}
```

### 11.2 卖出逻辑

卖出不应只依赖止盈止损，还应包括：

- thesis 失效。
- market regime 恶化。
- 板块热度退潮。
- 更优机会替代。
- 持仓超过计划周期。
- 风控降仓。

## 12. Memory System

Memory System 是 Cognitive Agent 的学习基础。

### 12.1 记忆类型

| 类型 | 示例 | 用途 |
| --- | --- | --- |
| Episodic Memory | 某一天的市场判断和交易 | 回放决策过程 |
| Trade Memory | 每笔交易的 thesis、执行、结果 | 相似案例检索 |
| Strategy Memory | 每个策略的表现和适用环境 | 策略权重调整 |
| Lesson Memory | 归因后得到的经验 | 决策前提醒 |
| Social Memory | 哪类内容表现好 | 内容策略优化 |
| User Preference Memory | 用户偏好和禁区 | 个性化约束 |

### 12.2 交易归因结构

```json
{
  "trade_id": "T20260514-001",
  "symbol": "xxxxxx",
  "strategy_id": "volume_breakout_v1",
  "market_regime_at_entry": "bear",
  "entry_thesis": "放量突破前高",
  "exit_reason": "stop_loss",
  "actual_return_pct": -5.1,
  "mistake_type": "false_breakout",
  "root_cause": "弱势市场中突破策略失败率上升",
  "lesson": "bear regime 下 volume_breakout_v1 需要降权或禁用",
  "strategy_adjustment": {
    "strategy_id": "volume_breakout_v1",
    "weight_delta": -0.15,
    "disable_in_regimes": ["bear"]
  }
}
```

### 12.3 决策前记忆检索

Strategist 在生成交易建议前，必须检索：

- 同一股票历史交易。
- 同一策略历史表现。
- 当前 market regime 下的类似交易。
- 最近 20 笔亏损教训。
- 与候选股形态相似的历史案例。

## 13. Strategy Lab

Strategy Lab 负责策略自我演进。

### 13.1 策略生命周期

```text
Idea → Draft → Backtest → Paper Experiment → Active → Degraded → Retired
```

| 阶段 | 说明 |
| --- | --- |
| Idea | Agent 从复盘、市场变化或社交反馈中提出假设 |
| Draft | 转换为结构化策略规则 |
| Backtest | 历史数据回测 |
| Paper Experiment | 小仓位/虚拟盘观察 |
| Active | 纳入正式策略池 |
| Degraded | 表现恶化，降权 |
| Retired | 长期失效，归档 |

### 13.2 策略定义

```yaml
strategy:
  id: hot_sector_pullback_v1
  name: 热点板块龙头回踩
  status: active
  compatible_regimes: ["bull", "neutral"]
  entry_conditions:
    - sector_rank <= 5
    - turnover_yi >= 5
    - price_above_ma20
    - pullback_to_ma5_or_ma10
  exit_rules:
    stop_loss_pct: 0.05
    take_profit_pct: 0.10
    max_holding_days: 5
  performance:
    win_rate: 0.63
    profit_factor: 1.45
    sample_size: 42
```

### 13.3 晋级条件

策略从 paper experiment 晋级 active，建议同时满足：

- 样本数 `>= 30`。
- 胜率 `>= 55%`。
- Profit Factor `>= 1.2`。
- 最大回撤低于阈值。
- 在至少两种市场阶段不过度失效。

## 14. Social Brain

Social Brain 负责把交易研究转化为稳定的社交影响力。

### 14.1 目标

- 每个交易日持续分享。
- 内容来自真实市场观察、交易计划、复盘和策略学习。
- 优化浏览量、互动率和长期信任。
- 从评论和内容表现中提取市场叙事反馈。

### 14.2 内容类型

| 类型 | 发布时间 | 内容来源 |
| --- | --- | --- |
| 盘前计划 | 盘前 | Market Brain |
| 收盘复盘 | 收盘后 | Reviewer / Memory |
| 策略笔记 | 晚间 | Strategy Lab |
| 错误复盘 | 交易关闭后 | Trade Attribution |
| 热点观察 | 市场异动时 | Market Brain / NarrativeTracker |

### 14.3 内容生成输入

```json
{
  "market_view": "...",
  "today_plan": "...",
  "position_changes": "...",
  "lessons": ["..."],
  "risk_disclaimer": "...",
  "target_audience": "普通投资者/短线交易者",
  "platform_style": "小红书/朋友圈/公众号"
}
```

### 14.4 内容指标反馈

每日回收：

- 浏览量。
- 点赞。
- 收藏。
- 评论。
- 关注增长。
- 高质量评论。
- 负反馈。

Social Brain 每周输出：

```json
{
  "best_topics": ["收盘复盘", "策略错误复盘"],
  "weak_topics": ["纯行情摘要"],
  "recommended_content_mix": {
    "daily_review": 0.5,
    "strategy_note": 0.3,
    "market_observation": 0.2
  },
  "next_week_goal": "提高收藏率，减少泛泛行情描述"
}
```

## 15. 数据模型扩展

### 15.1 新增核心表

建议在 Central Brain 中新增：

```text
agent_plans
market_regime_snapshots
trading_persona_versions
strategy_registry
strategy_experiments
trade_attributions
lessons
portfolio_snapshots
social_content_plans
social_metrics
agent_reflections
```

### 15.2 `market_regime_snapshots`

```sql
CREATE TABLE market_regime_snapshots (
  id TEXT PRIMARY KEY,
  trade_date TEXT NOT NULL,
  regime TEXT NOT NULL,
  risk_appetite TEXT,
  hot_sectors_json TEXT,
  recommended_posture TEXT,
  max_total_position_pct REAL,
  summary TEXT,
  evidence_json TEXT,
  created_at TEXT NOT NULL
);
```

### 15.3 `strategy_registry`

```sql
CREATE TABLE strategy_registry (
  strategy_id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  version TEXT NOT NULL,
  status TEXT NOT NULL,
  definition_yaml TEXT NOT NULL,
  win_rate REAL DEFAULT 0,
  profit_factor REAL DEFAULT 0,
  max_drawdown_pct REAL DEFAULT 0,
  sample_size INTEGER DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
```

### 15.4 `trade_attributions`

```sql
CREATE TABLE trade_attributions (
  attribution_id TEXT PRIMARY KEY,
  position_id TEXT NOT NULL,
  strategy_id TEXT,
  market_regime TEXT,
  actual_return_pct REAL,
  result TEXT,
  mistake_type TEXT,
  root_cause TEXT,
  lesson TEXT,
  adjustment_json TEXT,
  created_at TEXT NOT NULL
);
```

### 15.5 `social_metrics`

```sql
CREATE TABLE social_metrics (
  id TEXT PRIMARY KEY,
  post_id TEXT NOT NULL,
  platform TEXT NOT NULL,
  views INTEGER DEFAULT 0,
  likes INTEGER DEFAULT 0,
  comments INTEGER DEFAULT 0,
  saves INTEGER DEFAULT 0,
  shares INTEGER DEFAULT 0,
  collected_at TEXT NOT NULL
);
```

## 16. 工作流设计

### 16.1 每日盘前

```text
market.pre_open
  → AgentKernel
  → MarketBrain 生成市场初判
  → PortfolioManager 检查持仓风险
  → AgentKernel 生成 Daily Plan
  → SocialBrain 可选生成盘前观察
```

### 16.2 盘中

```text
market.tick
  → 检查持仓止损/止盈/thesis invalidation
  → 检查板块异动
  → 必要时触发 Strategist / RiskGovernor / Executioner
```

### 16.3 收盘后

```text
market.close
  → MarketBrain 生成收盘市场总结
  → Explorer 根据今日环境找候选
  → Strategist 生成明日关注/交易计划
  → Reviewer 复盘持仓和当日动作
  → MemorySystem 写入 lessons
  → SocialBrain 生成收盘复盘内容
```

### 16.4 周末

```text
weekly.review
  → StrategyPerformanceAnalyzer
  → TradeAttributionAgent
  → StrategyLab 提出/调整策略
  → SocialBrain 生成周复盘内容
```

## 17. 当前系统到目标系统的演进路径

### Phase 1：MarketBrain + TradingPersona + RiskGovernor

目标：从固定流水线升级为“先判断市场，再交易”。

新增：

```text
src/agents/cio/market_brain.py
src/agents/cio/trading_persona.py
src/agents/risk/risk_governor.py
config/trading_persona.yaml
```

改造：

```text
workflow:
  MarketBrain → Explorer → Strategist → RiskGovernor → Executioner → Influencer
```

验收：

- 每次 daily pipeline 都产生 market regime。
- 每条买入信号都经过 RiskGovernor。
- RiskGovernor 可 reduce/reject。
- 每次交易记录 market regime 和 strategy_id。

### Phase 2：结构化交易归因

目标：每笔交易关闭后自动生成 attribution 和 lesson。

新增：

```text
src/agents/memory/trade_attribution.py
src/agents/memory/lesson_extractor.py
```

验收：

- 每个 closed position 都有归因记录。
- Strategist 决策前能读取最近 lessons。
- weekly review 输出策略调整建议。

### Phase 3：Strategy Registry + Strategy Lab

目标：策略可以被版本化、回测、纸面实验、晋级和淘汰。

新增：

```text
src/agents/research/strategy_registry.py
src/agents/research/strategy_lab.py
src/agents/research/backtest_runner.py
```

验收：

- 每条交易信号绑定 strategy_id。
- 每个 strategy 有表现统计。
- 至少支持一个策略从 draft → paper → active。

### Phase 4：Social Brain 闭环

目标：社交内容不只是输出，而是有指标反馈和选题优化。

新增：

```text
src/agents/social/social_brain.py
src/agents/social/social_metrics_collector.py
src/agents/social/narrative_feedback.py
```

验收：

- 每日内容计划和实际内容可追踪。
- 每日浏览量进入 metrics。
- 每周生成内容表现复盘。
- 内容反馈可反哺 NarrativeTracker。

### Phase 5：Agent Kernel 事件驱动化

目标：从 cron 调度具体函数升级为事件驱动主脑。

改造：

```text
scheduler.py:
  不直接调用 daily_mock / review
  改为发布 market.pre_open / market.tick / market.close / weekly.review

AgentKernel:
  订阅事件
  生成任务
  调用子 Agent
```

验收：

- 固定任务和突发事件都走统一 AgentKernel。
- 支持任务队列和优先级。
- 支持人工插入指令。

## 18. 里程碑与阶段性指标

### M1：初级自主交易专家

能力：

- 每日 market regime。
- Trading Persona 生效。
- 风控 veto 生效。

指标：

- 所有交易 100% 经过风控。
- 每日计划生成率 `> 95%`。
- 社交日更率 `> 80%`。

### M2：会复盘的交易专家

能力：

- 每笔交易关闭后自动归因。
- 决策前检索历史 lesson。

指标：

- closed position 归因覆盖率 `> 90%`。
- 重复错误率逐周下降。
- 纸面交易胜率达到 `> 50%`。

### M3：会演进策略的交易专家

能力：

- 策略注册表。
- 策略回测。
- paper experiment。

指标：

- 至少 3 个 active/paper 策略。
- 每个 active 策略有独立绩效统计。
- 纸面交易胜率接近或超过 `55%`。

### M4：交易与内容双闭环

能力：

- 社交指标回收。
- 内容策略优化。
- 市场叙事反馈。

指标：

- 日更率 `> 90%`。
- 7 日平均浏览量 `> 3000`。
- 每周至少 3 条高质量评论/观点进入记忆。

### M5：目标状态

能力：

- Agent 能独立形成市场观点、交易计划、策略调整和内容计划。
- 用户主要做监督和关键风险批准。

指标：

- 已关闭交易胜率 `> 60%`。
- Profit Factor `> 1.3`。
- 最大回撤在设定阈值内。
- 每日社交圈浏览量 7 日均值 `> 5000`。

## 19. 风险与防护

### 19.1 交易风险

| 风险 | 防护 |
| --- | --- |
| 过度交易 | 每日交易次数和总仓位限制 |
| 过拟合 | 策略晋级需要样本数和跨市场阶段验证 |
| LLM 幻觉 | 所有判断必须绑定行情证据 |
| 连续亏损 | 自动降仓/停手 |
| 风格漂移 | Trading Persona 约束 |
| 单一数据源故障 | 多数据源 fallback |

### 19.2 内容风险

| 风险 | 防护 |
| --- | --- |
| 标题党 | 内容合规检查 |
| 观点和交易不一致 | 内容必须引用真实 market view / review |
| 过度承诺收益 | 禁止收益承诺 |
| 泄露敏感交易信息 | 脱敏和延迟发布策略 |

### 19.3 系统风险

| 风险 | 防护 |
| --- | --- |
| 自动执行失控 | paper/live 分级，live 需要显式批准 |
| 数据污染 | 数据质量检查和异常检测 |
| 记忆污染 | lesson 需要结构化、可追溯 |
| prompt 漂移 | persona 和策略配置版本化 |

## 20. 推荐下一步

建议立即进入 Phase 1：

```text
MarketBrain + TradingPersona + RiskGovernor
```

最小改造路径：

1. 新增 `config/trading_persona.yaml`。
2. 新增 `MarketBrainAgent`，输出 market regime 和 daily plan。
3. 新增 `RiskGovernor`，对 Strategist 的信号执行 approve/reduce/reject。
4. 修改 daily workflow：

```text
MarketBrain → Explorer → Strategist → RiskGovernor → Executioner → Influencer
```

5. 每条 signal / position 记录：

```text
market_regime
strategy_id
risk_decision
persona_version
```

完成 Phase 1 后，系统就会从“固定流程自动交易”升级为“有市场判断和风控约束的初级 Cognitive Agent”。
