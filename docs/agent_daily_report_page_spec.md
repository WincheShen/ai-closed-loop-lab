# Agent 日报页面 — 前端需求文档

> 给 Kimi 实现用。本页面用于查看每天 Agent 管道的选股、评估和操作记录。

## 1. 页面概述

**路由**: `/agent-report`（或挂在 `/agents` 下作为子页面）

**功能**: 以日为单位展示 AI Agent 管道的完整决策链 —— 从市场判断、选股扫描、深度评估到风控审核和最终执行。

**布局**: 顶部日期选择器 + 下方多个 Section 卡片

---

## 2. 数据源

### 2.1 后端 API（需要新建）

建议在 `src/webhook_listener/server.py` 中新增一个 router（或直接加路由）：

| Method | Path | 说明 |
|--------|------|------|
| GET | `/api/agent-report/dates` | 返回有数据的日期列表 |
| GET | `/api/agent-report/{date}` | 返回该日的完整报告数据 |

### 2.2 底层数据位置

| 数据 | 来源 | 格式 |
|------|------|------|
| 每日选股结果 | `data/daily_picks/{YYYY-MM-DD}.json` | JSON 文件 |
| 市场制度快照 | `data/central_brain.db` → `market_regime_snapshots` | SQLite |
| 交易信号 | `data/central_brain.db` → `trade_signals` | SQLite |
| 风控裁决 | `data/central_brain.db` → `risk_decisions` | SQLite |
| 订单记录 | `data/central_brain.db` → `orders` + `fills` | SQLite |
| LLM 调用统计 | `data/central_brain.db` → `llm_calls` | SQLite |

---

## 3. 页面结构

### 3.1 顶部 — 日期选择 + 摘要条

```
┌──────────────────────────────────────────────────────────────────┐
│  📅 [2026-05-25 ▼]    市场: 🟢 rebound    姿态: selective_attack │
│                        热点: 有色金属 · 人工智能 · 电力            │
│  统计: 扫描 5511 只 → 候选 30 只 → Agent 分析 8 只 → 买入 2 只   │
│  耗时: 125s    LLM 成本: $0.45                                   │
└──────────────────────────────────────────────────────────────────┘
```

**字段映射**:
- `regime` / `recommended_posture` → 来自 `market_regime_snapshots`
- `hot_sectors` → 来自 `daily_picks.json` 或 `market_regime_snapshots.hot_sectors_json`
- 统计数字 → 来自 `daily_picks_archive` 表
- LLM 成本 → 来自 `daily_picks_archive.total_llm_cost_usd`

---

### 3.2 Section A — 市场判断 (MarketBrain)

展示当日 `MarketRegimeSnapshot`：

| 字段 | 说明 | 展示方式 |
|------|------|---------|
| `regime` | bull/neutral/bear/panic/rebound | 彩色标签 |
| `risk_appetite` | high/medium/low | 文字 |
| `recommended_posture` | attack/selective_attack/defend/observe/exit | 标签 |
| `max_total_position_pct` | 最大仓位 | 百分比进度条 |
| `hot_sectors` | 热点板块 | Tag 列表 |
| `dominant_styles` | 推荐风格 | Tag 列表 |
| `avoid_styles` | 避免风格 | 灰色 Tag |
| `strategy_bias` | 策略权重 | 横向柱形图 {"hot_sector_pullback": 0.45, ...} |
| `daily_questions` | 今日观察问题 | 列表 |
| `summary` | 一句话总结 | 引用块 |
| `evidence` | 量化数据 | 小卡片组 (涨/跌/平/强势/弱势/均涨幅) |

---

### 3.3 Section B — 选股结果 (DailyScan)

三个 Tab 或三列：**激进推荐** / **稳健推荐** / **候选池 (Top 30)**

每只股票卡片/行：

| 字段 | 类型 | 说明 |
|------|------|------|
| `symbol` | string | 股票代码 |
| `name` | string | 股票名称 |
| `price` | float | 当前价格 |
| `change_pct` | float | 涨跌幅 (%) — 红涨绿跌 |
| `industry` | string | 所属行业 |
| `rule_score` | float | 规则引擎评分 (0-10) |
| `matched_rules` | string[] | 命中规则列表 |
| `agent_decision` | "BUY"/"HOLD"/"PASS"/null | Agent 决策 |
| `agent_confidence` | float/null | 置信度 (0-1) |
| `agent_summary` | string/null | Agent 分析摘要 |
| `bucket` | string | 分类: aggressive/stable/candidate |
| `reasoning` | string | 分类理由 |

**交互**:
- 候选池默认折叠，只显示数量
- 点击展开看完整表格
- 表头可排序（按 score / change_pct）

---

### 3.4 Section C — 深度评估 (Strategist 信号)

展示当日生成的 `TradeSignal` 列表（来自 `trade_signals` 表）：

| 字段 | 类型 | 说明 |
|------|------|------|
| `signal_id` | string | 信号 ID |
| `symbol` | string | 股票代码 |
| `action` | "buy"/"sell"/"hold" | 决策 |
| `entry_price` | float | 建议入场价 |
| `target_price` | float | 目标价 |
| `stop_loss` | float | 止损价 |
| `position_pct` | float | 建议仓位 (2%-10%) |
| `strategy` | string | 策略名 (如 "热点板块前排回踩") |
| `confidence` | float | 置信度 |
| `rationale` | string | 决策理由 (2-3句) |
| `bull_case` | string | 看多逻辑 |
| `bear_case` | string | 风险点 |

**展示建议**:
- 每个信号一个卡片
- `action=buy` 绿色/红色边框，`action=pass` 灰色
- 目标价/止损价/入场价可以用一个简单的价格刻度图展示风险收益比

---

### 3.5 Section D — 风控审核 (RiskGovernor)

展示 `risk_decisions` 表数据：

| 字段 | 类型 | 说明 |
|------|------|------|
| `signal_id` | string | 关联信号 |
| `symbol` | string | 股票代码 |
| `decision` | "approve"/"reduce"/"reject" | 裁决 |
| `original_position_pct` | float | 原始仓位 |
| `approved_position_pct` | float | 批准仓位 |
| `reason` | string | 理由 |
| `risk_flags` | string[] | 风险标志 |

**展示建议**:
- approve → 绿色 ✓
- reduce → 黄色 ⚠ (显示原始 → 调整后仓位)
- reject → 红色 ✗

---

### 3.6 Section E — 执行记录 (Executor)

展示 `orders` + `fills` 表数据：

| 字段 | 类型 | 说明 |
|------|------|------|
| `order_id` | string | 订单 ID |
| `symbol` | string | 股票代码 |
| `side` | "buy"/"sell" | 方向 |
| `quantity` | int | 数量 |
| `order_type` | "market"/"limit" | 类型 |
| `limit_price` | float | 限价 |
| `status` | string | 状态 |
| `avg_price` | float | 成交均价 (来自 fill) |
| `fees` | float | 手续费 |
| `filled_at` | string | 成交时间 |

---

## 4. API 响应格式建议

```typescript
// GET /api/agent-report/{date}
interface AgentDailyReport {
  date: string                    // "2026-05-25"
  
  // 市场判断
  market_regime: {
    regime: string
    risk_appetite: string
    recommended_posture: string
    max_total_position_pct: number
    hot_sectors: string[]
    dominant_styles: string[]
    avoid_styles: string[]
    strategy_bias: Record<string, number>
    daily_questions: string[]
    summary: string
    evidence: {
      up_count: number
      down_count: number
      flat_count: number
      strong_count: number
      weak_count: number
      avg_change_pct: number
      up_ratio: number
      total_stocks: number
    }
  } | null
  
  // 选股结果
  picks: {
    hot_sectors: string[]
    aggressive: StockPick[]
    stable: StockPick[]
    candidates: StockPick[]
    candidates_count: number
    agent_calls_count: number
    elapsed_seconds: number
  } | null
  
  // 交易信号
  signals: TradeSignal[]
  
  // 风控裁决
  risk_decisions: RiskDecision[]
  
  // 执行记录
  orders: OrderWithFill[]
  
  // 成本统计
  cost: {
    total_llm_cost_usd: number
    total_calls: number
    total_tokens: number
  }
}

interface StockPick {
  symbol: string
  name: string
  price: number
  change_pct: number
  industry: string
  rule_score: number
  matched_rules: string[]
  agent_decision: string | null
  agent_confidence: number | null
  agent_summary: string | null
  bucket: "aggressive" | "stable" | "candidate"
  reasoning: string
}

interface TradeSignal {
  signal_id: string
  symbol: string
  action: "buy" | "sell" | "hold"
  entry_price: number
  target_price: number
  stop_loss: number
  position_pct: number
  strategy: string
  confidence: number
  rationale: string
  bull_case: string
  bear_case: string
  timestamp: string
}

interface RiskDecision {
  signal_id: string
  symbol: string
  decision: "approve" | "reduce" | "reject"
  original_position_pct: number
  approved_position_pct: number
  reason: string
  risk_flags: string[]
}

interface OrderWithFill {
  order_id: string
  signal_id: string
  symbol: string
  side: "buy" | "sell"
  quantity: number
  order_type: string
  limit_price: number | null
  status: string
  submitted_at: string
  // fill info
  avg_price: number | null
  fees: number | null
  filled_at: string | null
}
```

---

## 5. 设计参考

- 整体风格沿用现有深色主题 (data-card, panel, accent 色)
- 参考现有 `AgentWorkspace.tsx` 的工作流节点 + 日志面板风格
- 参考 `Strategy.tsx` 的表格和卡片风格
- regime 标签配色: bull=红/bullish, bear=绿/bearish, neutral=灰, rebound=蓝, panic=紫

---

## 6. 后端 API 实现提示

```python
# src/webhook_listener/server.py 中新增路由

@app.get("/api/agent-report/dates")
def list_report_dates():
    """列出有选股数据的日期"""
    import glob
    files = sorted(glob.glob("data/daily_picks/*.json"), reverse=True)
    dates = [Path(f).stem for f in files]
    return dates

@app.get("/api/agent-report/{date}")  
def get_agent_report(date: str):
    """获取某天的完整 Agent 报告"""
    # 1. 读 data/daily_picks/{date}.json
    # 2. 查 market_regime_snapshots WHERE trade_date = date
    # 3. 查 trade_signals WHERE DATE(timestamp) = date
    # 4. 查 risk_decisions WHERE DATE(created_at) = date
    # 5. 查 orders + fills WHERE DATE(submitted_at) = date
    # 6. 查 llm_calls 聚合 WHERE DATE(ts) = date
    # 组装返回
```

---

## 7. 现有数据示例

**daily_picks/2026-05-01.json** 的样本 (mock 数据):
```json
{
  "pick_date": "2026-05-01",
  "is_mock_data": true,
  "hot_sectors": ["半导体", "光伏储能", "创新药", "AI手机", "白酒"],
  "aggressive": [
    {
      "symbol": "600011",
      "name": "模拟股011",
      "price": 36.4,
      "change_pct": 3.7,
      "industry": "白酒",
      "rule_score": 7.0,
      "matched_rules": ["not_st", "market_cap_range", "in_hot_sector", "volume_breakout", "strong_turnover", "main_fund_inflow"],
      "agent_decision": "BUY",
      "agent_confidence": 0.82,
      "agent_summary": "[MOCK] 600011 当前 43.90，建议 BUY",
      "bucket": "aggressive",
      "reasoning": "规则得分 7.0 + Agent BUY(置信 82%) + 当日 +3.7%"
    }
  ],
  "stable": [...],
  "candidates": [/* 30 items */]
}
```

**market_regime_snapshots** 样本 (真实数据):
```json
{
  "trade_date": "2026-05-25",
  "regime": "rebound",
  "risk_appetite": "high",
  "recommended_posture": "selective_attack",
  "max_total_position_pct": 0.5,
  "hot_sectors": ["有色金属", "人工智能", "医疗器械", "电力"],
  "strategy_bias": {"hot_sector_pullback": 0.45, "volume_breakout": 0.35, "defensive_bluechip": 0.1, "mean_reversion": 0.1},
  "evidence": {"up_count": 3371, "down_count": 1951, "strong_count": 105, "avg_change_pct": 0.58, "total_stocks": 5511}
}
```
