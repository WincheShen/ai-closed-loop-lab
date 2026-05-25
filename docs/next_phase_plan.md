# AI Closed Loop Lab — 下一阶段行动计划

> 作者：总架构师  
> 日期：2026-05-01  
> 状态：待审阅  
> 前置文档：`cognitive_agent_design.md`, `phase1_assessment.md`

---

## 0. 当前状态判定

### 里程碑进度

| 里程碑 | 目标 | 状态 | 卡点 |
|--------|------|------|------|
| M1 初级自主交易 | regime + persona + 风控 | **架构完成，未持续运转** | 没有每日跑 LangGraph 管线 |
| M2 会复盘的交易 | 归因 + lesson 检索 | ❌ 未开始 | — |
| M3 会演进策略的交易 | Strategy Lab + 回测 | ❌ 未开始 | — |
| M4 交易与内容双闭环 | 社交指标回收 + 优化 | ~15% | — |
| M5 目标状态 | 胜率>60% 浏览>5000 | ❌ | 12个月级 |

### 结构性问题

1. **两套管线并存** — Scheduler 调的是老 `DailyScanPipeline`，不是 LangGraph 管线
2. **反馈环未闭合** — 平仓不写归因，Strategist 不读 lesson，策略权重不更新
3. **热点板块瘸腿** — NAS 上 eastmoney 被封，Sina 无板块，persona 最强招式失效
4. **Influencer 空壳** — 只有模板文案，无 SMA 实际发布，无指标回收
5. **数据荒** — bear 市场规则太严 → 0 交易 → 无法积累样本

### 核心判断

> **当前最高优先级不是写新功能，而是让系统每天跑起来、积累数据。**
> 没有数据，Phase 2 归因无燃料，Phase 3 策略实验无基准，一切进化无从谈起。

---

## 1. Sprint 路线图

```
Sprint 0 (1周)     Sprint 1 (2周)       Sprint 2 (1周)       Sprint 3 (2周)
─────────────────  ──────────────────   ──────────────────   ──────────────────
稳定 & 跑起来       交易归因 & 学习        管线统一 & 清理        Social Brain MVP
                                                            
S0.1 管线切换       S1.1 归因引擎         S2.1 废弃老管线       S3.1 内容策略引擎
S0.2 热点数据修复   S1.2 Lesson 存储      S2.2 入口统一         S3.2 指标回收
S0.3 规则放宽       S1.3 Strategist       S2.3 文档清理         S3.3 选题优化
S0.4 健康监控         读取 lesson                             S3.4 前端集成
                   S1.4 周复盘自动化                          
                   S1.5 前端归因页面                          
```

---

## 2. Sprint 0：稳定 & 跑起来 (目标：M1 真正达标)

**交付标准**：Scheduler 每个交易日自动跑 LangGraph 管线，Agent 日报有真实数据。

### S0.1 Scheduler 切换到 LangGraph 管线

**问题**：`scheduler.py` 里 `job_daily_scan()` 调的是老 `DailyScanPipeline`，
跟 `cognitive_agent_design.md` 设计的 LangGraph 管线完全脱节。

**改造**：

```python
# scheduler.py — 改造前
def job_daily_scan():
    from stock_analyzer.pipelines.daily_scan import DailyScanPipeline
    pipeline = DailyScanPipeline()
    pipeline.run()

# scheduler.py — 改造后
def job_daily_pipeline():
    """每日完整 LangGraph 管线：
    MarketBrain → Explorer → Strategist → RiskGovernor → Executioner → Influencer
    """
    from graph.workflow import run_daily_pipeline
    result = run_daily_pipeline(mode="mock")
    logger.info("Daily pipeline done: %s", result.get("logs", [])[-1:])
```

**调度表调整**：

| 时间 | 改造前 | 改造后 |
|------|--------|--------|
| 09:35 | `job_market_brain_only()` | 保留不变 |
| 15:35 | `job_daily_scan()` (老管线) | `job_daily_pipeline()` (LangGraph) |
| 15:40 | `job_daily_mock()` | 删除 (Executioner 已在 pipeline 里) |

**文件变更**：`scripts/scheduler.py`

### S0.2 修复热点板块检测

**问题**：NAS 上 push2.eastmoney 被封 → `snapshot.sectors = []` → `hot_sectors = []`
→ persona 的 `hot_sector_pullback` 策略永远不命中。

**方案**：EmappdataHotSectorDetector 已经存在，但没被 MarketBrain 调用。
需要在 MarketBrain 里加 fallback 链：

```python
# market_brain.py — 热点板块检测链
def _detect_hot_sectors(self, snapshot: MarketSnapshot) -> list[str]:
    # 1. 优先用 snapshot 里的板块数据 (eastmoney)
    if snapshot.sectors:
        detector = HotSectorDetector()
        scores = detector.detect(snapshot, top_k=5)
        if scores:
            return [s.sector.name for s in scores]

    # 2. fallback: emappdata 热度榜推断
    from stock_analyzer.data_source.emappdata_hot_sector import EmappdataHotSectorDetector
    emap = EmappdataHotSectorDetector(top_k=100)
    stock_names = {s.symbol: s.name for s in snapshot.stocks}
    sector_quotes = emap.detect(stock_names)
    if sector_quotes:
        return [sq.name for sq in sector_quotes[:5]]

    # 3. 全部失败
    return []
```

**文件变更**：`src/agents/cio/market_brain.py`

### S0.3 放宽 bear 市规则

**问题**：`phase1_assessment.md` 已指出 — bear/panic 市场下 hot_sector_pullback
和 volume_breakout 都是 forbidden，只剩 defensive_bluechip 一条路 → 0 交易 → 无样本。

**改造 `config/trading_persona.yaml`**：

```yaml
strategy_regime_compatibility:
  hot_sector_pullback:
    compatible: [bull, neutral, rebound]
    degraded: [bear, panic]         # 原 forbidden → degraded
    forbidden: []                   # 不再直接禁止
  volume_breakout:
    compatible: [bull, rebound]
    degraded: [bear, neutral]       # neutral 原来也是 degraded
    forbidden: [panic]              # 只在 panic 禁止
  defensive_bluechip:
    compatible: [bull, neutral, bear, panic, rebound]
    degraded: []
    forbidden: []
  mean_reversion:
    compatible: [bear, neutral]
    degraded: [panic]
    forbidden: []
```

RiskGovernor 遇到 degraded 策略会自动 reduce 仓位 50%，不会全拒。
这样 bear 市至少能积累小仓位样本。

**文件变更**：`config/trading_persona.yaml`

### S0.4 每日健康监控

新增一个轻量级健康检查脚本，Scheduler 每天 16:00 自动跑：

```python
# scripts/daily_health_check.py
def check():
    """检查今日各节点是否正常运转，输出到日志。"""
    today = date.today().isoformat()
    brain = get_central_brain()

    checks = {
        "market_regime": brain.store.count_regime_snapshots(today) > 0,
        "candidates": brain.store.count_daily_picks(today) > 0,
        "signals": brain.store.count_signals(today) >= 0,  # 0 也可以
        "risk_decisions": brain.store.count_risk_decisions(today) >= 0,
        "pipeline_ran": brain.store.has_session_today(today),
    }

    for name, ok in checks.items():
        status = "OK" if ok else "MISSING"
        logger.info("[HealthCheck] %s: %s", name, status)

    if not all(checks.values()):
        logger.warning("[HealthCheck] 今日有节点未产出数据！")
```

**文件变更**：`scripts/daily_health_check.py`, `scripts/scheduler.py`

---

## 3. Sprint 1：交易归因 & 学习闭环 (目标：M2)

**交付标准**：每笔平仓自动生成结构化归因，Strategist 决策前能读取最近 lesson。

### 架构设计

```
Position 平仓
    ↓
TradeAttributor                         ┌─────────────┐
  ├─ 对比 entry 时 regime vs close 时 regime  │  lessons 表  │
  ├─ 对比 original_thesis vs 实际走势          │  (SQLite)   │
  ├─ LLM 生成 attribution + lesson            └──────┬──────┘
  ↓                                              ↓
LessonStore.save()                      Strategist.decide()
                                          ├─ 读取最近 5 条 lesson
                                          └─ 注入 system prompt
```

### S1.1 新建归因引擎

**新文件**：`src/agents/memory/trade_attribution.py`

```python
"""交易归因引擎 — 每笔平仓自动生成结构化归因。"""

@dataclass
class TradeAttribution:
    """单笔交易归因记录。"""
    attribution_id: str
    position_id: str
    symbol: str

    # 结果
    entry_price: float
    close_price: float
    realized_pnl: float
    pnl_pct: float
    holding_days: int

    # 归因分解
    outcome: str             # win / loss / breakeven
    primary_cause: str       # 主因分类 (见下)
    secondary_causes: list[str]

    # 上下文对比
    entry_regime: str        # 买入时 regime
    close_regime: str        # 卖出时 regime
    regime_changed: bool     # 持仓期间 regime 是否变化
    strategy_id: str
    original_thesis: str     # 买入逻辑
    actual_narrative: str    # 实际走势叙事

    # LLM 生成
    lesson: str              # 一句话教训
    should_have: str         # "如果重来，应该..."
    tags: list[str]          # 可检索标签

    created_at: str


class TradeAttributor:
    """归因引擎。"""

    # 主因分类枚举
    CAUSES = [
        "thesis_correct",          # 逻辑正确，按计划盈利
        "thesis_wrong",            # 选股逻辑错误
        "timing_early",            # 时机太早
        "timing_late",             # 追高
        "regime_shift",            # 市场环境突变
        "stop_loss_triggered",     # 正常止损
        "take_profit_triggered",   # 正常止盈
        "position_too_large",      # 仓位过重放大亏损
        "held_too_long",           # 超出持仓期限
        "external_shock",          # 外部冲击 (政策/黑天鹅)
    ]

    def attribute(self, position: dict) -> TradeAttribution:
        """对已关闭仓位生成归因。

        1. 从 DB 拉取 entry/close 时的 regime snapshot
        2. 对比 original_thesis vs 实际价格走势
        3. 调用 LLM 生成 lesson
        """
        ...

    def _build_llm_prompt(self, position: dict, entry_regime: dict,
                          close_regime: dict) -> str:
        """构造归因 prompt。"""
        ...
```

**主因分类说明**：

| primary_cause | 含义 | 对应改进方向 |
|---------------|------|-------------|
| thesis_correct | 选对了 | 加强该策略权重 |
| thesis_wrong | 选股逻辑错 | 检查 rule engine / LLM prompt |
| timing_early | 买早了 | 调整入场条件 |
| timing_late | 追高了 | 加严涨幅过滤 |
| regime_shift | 市场突变 | 加快 regime 更新频率 |
| stop_loss_triggered | 正常止损 | 检查止损位设置 |
| held_too_long | 持仓太久 | 缩短持仓天数限制 |

### S1.2 Lesson 存储

**数据库扩展** — 在 `metadata_store.py` 新增两张表：

```sql
CREATE TABLE IF NOT EXISTS trade_attributions (
    attribution_id TEXT PRIMARY KEY,
    position_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    entry_price REAL,
    close_price REAL,
    realized_pnl REAL,
    pnl_pct REAL,
    holding_days INTEGER,
    outcome TEXT NOT NULL,        -- win/loss/breakeven
    primary_cause TEXT NOT NULL,
    secondary_causes_json TEXT,
    entry_regime TEXT,
    close_regime TEXT,
    regime_changed INTEGER DEFAULT 0,
    strategy_id TEXT,
    original_thesis TEXT,
    actual_narrative TEXT,
    lesson TEXT,
    should_have TEXT,
    tags_json TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (position_id) REFERENCES positions(position_id)
);

CREATE TABLE IF NOT EXISTS lessons (
    lesson_id TEXT PRIMARY KEY,
    attribution_id TEXT,
    symbol TEXT,
    strategy_id TEXT,
    regime TEXT,
    outcome TEXT,              -- win/loss
    lesson_text TEXT NOT NULL,
    tags_json TEXT,
    relevance_score REAL DEFAULT 1.0,
    cited_count INTEGER DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_lessons_strategy ON lessons(strategy_id);
CREATE INDEX IF NOT EXISTS idx_lessons_regime ON lessons(regime);
CREATE INDEX IF NOT EXISTS idx_lessons_outcome ON lessons(outcome);
CREATE INDEX IF NOT EXISTS idx_attr_position ON trade_attributions(position_id);
```

**查询接口**：

```python
class MemoryStore:
    def get_recent_lessons(
        self,
        strategy_id: str | None = None,
        regime: str | None = None,
        limit: int = 5,
    ) -> list[dict]:
        """检索最近 lesson，按相关度排序。

        优先返回：同策略 + 同 regime 的 lesson。
        """
        ...

    def save_attribution(self, attr: dict) -> None: ...
    def save_lesson(self, lesson: dict) -> None: ...
```

### S1.3 Strategist 读取 Lesson

**改造 `signal_generator.py`**：在 LLM prompt 中注入最近相关 lesson。

```python
STRATEGIST_USER_TEMPLATE = """\
{persona_block}

## 今日市场作战指令 (来自 MarketBrain)
...

## 历史教训 (来自 Memory)
{lessons_block}

## 候选股票
...
"""

class StrategistEngine:
    def _lessons_block(self, candidate: StockCandidate) -> str:
        """从 lesson 库检索与当前决策相关的历史教训。"""
        regime = self.market_regime.get("regime", "neutral")
        # 策略相关 lesson (最多 3 条)
        strategy_lessons = self.brain.store.get_recent_lessons(
            regime=regime, limit=3
        )
        # 同板块 lesson (最多 2 条)
        sector = candidate.get("sector", "")
        sector_lessons = self.brain.store.get_recent_lessons(
            tags=[sector], limit=2
        ) if sector else []

        if not strategy_lessons and not sector_lessons:
            return "暂无历史教训记录。"

        lines = []
        for l in strategy_lessons + sector_lessons:
            lines.append(
                f"- [{l['outcome']}] {l['strategy_id']}/{l['regime']}: "
                f"{l['lesson_text']}"
            )
        return "\n".join(lines[:5])
```

### S1.4 自动触发归因

**触发时机**：Position 从 open → closed 时自动归因。

改造 `executor.py` 的平仓逻辑 + Reviewer 的 EXIT 决策：

```python
# 在 position 被关闭后
def _on_position_closed(self, position: dict):
    """仓位关闭后自动触发归因。"""
    from agents.memory.trade_attribution import TradeAttributor
    attributor = TradeAttributor(self.session_id)
    attribution = attributor.attribute(position)
    self.brain.store.save_attribution(attribution.to_dict())
    self.brain.store.save_lesson({
        "lesson_id": f"LSN-{uuid.uuid4().hex[:8]}",
        "attribution_id": attribution.attribution_id,
        "symbol": attribution.symbol,
        "strategy_id": attribution.strategy_id,
        "regime": attribution.entry_regime,
        "outcome": attribution.outcome,
        "lesson_text": attribution.lesson,
        "tags_json": json.dumps(attribution.tags),
        "created_at": datetime.now().isoformat(),
    })
```

### S1.5 周复盘自动化

改造现有 `backtest_engine.py`，把散落的 attribution 汇总为周报：

```python
def run_weekly_review(self) -> dict:
    """周复盘：汇总本周归因，生成策略调整建议。"""
    week_attributions = self.brain.store.get_attributions_since(days=7)

    # 1. 统计
    stats = {
        "total_trades": len(week_attributions),
        "win_rate": wins / total if total else 0,
        "avg_pnl_pct": mean(pnl_pcts),
        "cause_distribution": Counter(a["primary_cause"] for a in week_attributions),
        "strategy_performance": self._strategy_breakdown(week_attributions),
    }

    # 2. LLM 生成调整建议
    suggestions = self._llm_suggest(stats, week_attributions)

    # 3. 可选：自动微调 prompt_evolution 权重
    if self.auto_evolve:
        self.prompt_evolution.update_from_review(stats)

    return {"stats": stats, "suggestions": suggestions}
```

**调度**：Scheduler 周日 20:00 `job_weekly_review()` 调用。

### S1.6 前端归因展示

在 Agent 日报页面新增 "交易归因" Tab：

```
AgentDailyReport.tsx
└─ 新 Tab: 交易归因
    ├─ 已关闭仓位列表 (symbol, pnl, holding_days, outcome)
    ├─ 点击展开：归因详情
    │   ├─ primary_cause 标签
    │   ├─ entry_regime vs close_regime
    │   ├─ original_thesis vs actual_narrative
    │   └─ lesson + should_have
    └─ 底部：本周归因统计饼图 (按 primary_cause 分布)
```

**API**：

```
GET /api/agent-report/{date}
  → response.attributions: list[TradeAttribution]

GET /api/lessons?strategy_id=xxx&regime=xxx&limit=10
  → list[Lesson]
```

---

## 4. Sprint 2：管线统一 & 清理

**交付标准**：只剩一条管线，一个入口，文档清理完毕。

### S2.1 废弃老管线

| 动作 | 文件 |
|------|------|
| 删除 | `src/stock_analyzer/pipelines/daily_scan.py` (350行, `DailyScanPipeline`) |
| 删除 | `src/ai_platform/central_brain/workflow_engine/` (老 WorkflowEngine) |
| 删除 | `scripts/run_daily_workflow.py` (调老引擎) |
| 保留 | `src/graph/workflow.py` (LangGraph, 唯一管线) |
| 保留 | `scripts/run_full_loop.py` → 改名为 `scripts/run_pipeline.py` |

`run_daily_scan.py` 保留但重写为调用 LangGraph 管线的 scan-only 模式：

```python
# scripts/run_daily_scan.py — 改造后
"""快速选股（不执行交易）— 调用 LangGraph 管线的 scan 模式。"""
from graph.workflow import run_daily_pipeline
result = run_daily_pipeline(mode="scan")
```

### S2.2 Scheduler 入口统一

改造后的 `scheduler.py` 只调用 3 个函数：

```python
def job_market_brain():       # graph.workflow.run_market_brain_only()
def job_daily_pipeline():     # graph.workflow.run_daily_pipeline(mode="mock")
def job_intraday_review():    # agents.reviewer.position_reviewer.review_all()
def job_closing_analysis():   # agents.reviewer.closing_analysis.run()
def job_weekly_review():      # feedback_loop.backtest_engine.run_weekly_review()
def job_health_check():       # scripts.daily_health_check.check()
```

### S2.3 文档清理

| 文件 | 动作 |
|------|------|
| `docs/phase1.md` ~ `phase3.md` | 标记为 "已完成/归档" |
| `docs/phase3.5.md` | 标记 S1-S3 完成，S4 改为前端替代 |
| `docs/operations.md` | 删除，被 `deployment.md` 取代 |
| `docs/operations_manual.md` | 删除，同上 |
| `docs/nas-deployment.md` | 删除，同上 |
| `docs/runtime_overview.md` | 合入 `system_architecture.md` |
| `README.md` | 更新架构图 + 命令说明 |

---

## 5. Sprint 3：Social Brain MVP (目标：M4 起步)

**交付标准**：每日自动生成内容并发布到 SMA，浏览量指标可回收。

### S3.1 内容策略引擎

**新文件**：`src/agents/social/content_strategy.py`

不再只从 fill 生成帖子，而是根据当日数据**选择最佳选题角度**：

```python
class ContentStrategy:
    """内容策略引擎 — 选择今日最佳发布角度。"""

    ANGLES = [
        "daily_regime",        # "今日AI判定市场为XX，我做了什么"
        "trade_execution",     # "AI选出的XX股已买入，逻辑复盘"
        "position_review",     # "盘中AI复审，XX股决定继续持有"
        "closing_summary",     # "收盘总结：AI今日表现"
        "lesson_learned",      # "上周亏了XX，AI归因分析说..."
        "hot_sector_analysis", # "AI视角：今日半导体板块为何大涨"
    ]

    def pick_angle(self, state: DailyState) -> str:
        """根据今日数据选择最有内容价值的角度。"""
        if state.has_new_fills:
            return "trade_execution"
        if state.regime_changed:
            return "daily_regime"
        if state.has_closed_positions:
            return "lesson_learned"
        return "closing_summary"

    def generate(self, angle: str, state: DailyState) -> Post:
        """根据角度生成完整帖子 (标题 + 正文 + 标签)。"""
        ...
```

### S3.2 SMA 实际发布集成

改造 `content_engine.py`，从 placeholder 变为真正调用 SMA：

```python
async def publish_post(self, post: Post, account_id: str = "XHS_02") -> Post:
    from social_media_dispatcher.client import SMAClient
    client = SMAClient()
    result = await client.dispatch_topic({
        "account_id": account_id,
        "topic": post["title"],
        "content": post["content"],
        "tags": post.get("tags", []),
    })
    post["url"] = result.post_url
    post["sma_task_id"] = result.task_id
    # 持久化到 social_posts 表
    self.brain.store.save_social_post(...)
    return post
```

### S3.3 指标回收

**新文件**：`src/agents/social/metrics_collector.py`

```python
class SocialMetricsCollector:
    """每日回收 SMA 帖子的互动指标。"""

    def collect(self):
        """从 SMA API 拉取所有未关闭帖子的最新指标。"""
        posts = self.brain.store.list_recent_social_posts(days=7)
        for post in posts:
            metrics = self.sma_client.get_post_metrics(post["sma_task_id"])
            self.brain.store.update_social_metrics(
                post["sma_task_id"],
                views=metrics.views,
                likes=metrics.likes,
                comments=metrics.comments,
                shares=metrics.shares,
            )
```

**数据库扩展**：`social_posts` 表新增字段：

```sql
ALTER TABLE social_posts ADD COLUMN views INTEGER DEFAULT 0;
ALTER TABLE social_posts ADD COLUMN likes INTEGER DEFAULT 0;
ALTER TABLE social_posts ADD COLUMN comments INTEGER DEFAULT 0;
ALTER TABLE social_posts ADD COLUMN shares INTEGER DEFAULT 0;
ALTER TABLE social_posts ADD COLUMN content_angle TEXT;
```

**调度**：Scheduler 每天 21:00 `job_collect_social_metrics()`.

### S3.4 前端集成

ContentPipeline.tsx 页面增强：

```
ContentPipeline.tsx
├─ 已有: 待发布/审核中/已发布列表
└─ 新增:
    ├─ 7日互动趋势图 (views/likes/comments 折线)
    ├─ 内容角度分布饼图 (daily_regime vs trade_execution vs ...)
    └─ 最佳表现帖子 Top 5
```

---

## 6. 功能清单汇总

### Sprint 0 — 稳定 & 跑起来

| # | 功能 | 改动文件 | 工作量 |
|---|------|---------|--------|
| S0.1 | Scheduler 切换到 LangGraph | `scheduler.py` | 小 |
| S0.2 | MarketBrain 热点板块 fallback 链 | `market_brain.py` | 中 |
| S0.3 | 放宽 bear 市策略兼容性 | `trading_persona.yaml` | 小 |
| S0.4 | 每日健康检查脚本 | 新 `daily_health_check.py`, `scheduler.py` | 小 |

### Sprint 1 — 交易归因 & 学习

| # | 功能 | 改动文件 | 工作量 |
|---|------|---------|--------|
| S1.1 | TradeAttributor 归因引擎 | 新 `agents/memory/trade_attribution.py` | 大 |
| S1.2 | DB: attributions + lessons 表 | `metadata_store.py` | 中 |
| S1.3 | Strategist 注入 lesson context | `signal_generator.py` | 中 |
| S1.4 | 平仓自动触发归因 | `executor.py`, `position_reviewer.py` | 中 |
| S1.5 | 周复盘自动化 | `backtest_engine.py` | 中 |
| S1.6 | 前端归因 Tab + API | `AgentDailyReport.tsx`, `server.py` | 中 |

### Sprint 2 — 管线统一

| # | 功能 | 改动文件 | 工作量 |
|---|------|---------|--------|
| S2.1 | 废弃老管线代码 | 删除 `daily_scan.py`, `workflow_engine/` 等 | 小 |
| S2.2 | 统一 Scheduler 入口 | `scheduler.py` | 小 |
| S2.3 | 文档清理 | `docs/*.md`, `README.md` | 小 |

### Sprint 3 — Social Brain MVP

| # | 功能 | 改动文件 | 工作量 |
|---|------|---------|--------|
| S3.1 | ContentStrategy 选题引擎 | 新 `agents/social/content_strategy.py` | 大 |
| S3.2 | SMA 实际发布 | `content_engine.py`, `client.py` | 中 |
| S3.3 | 指标回收 | 新 `agents/social/metrics_collector.py` | 中 |
| S3.4 | 前端互动趋势 | `ContentPipeline.tsx`, `server.py` | 中 |

---

## 7. 不做的事 (显式推迟)

| 功能 | 原因 | 推迟到 |
|------|------|--------|
| 实盘交易 (live mode) | 需要券商 API + 大量测试，当前 mock 积累数据更重要 | M3 之后 |
| Strategy Lab (策略注册/回测/晋级) | 需要先有足够归因数据 | Sprint 1 完成后 |
| Agent Kernel 事件驱动化 | 当前 cron 调度够用，过早抽象增加复杂度 | M4 之后 |
| Redis EventBus | 单机部署 InMemory 够用 | 多机部署时 |
| sqlite-vec 向量检索 | lesson 量级 <1000 条，关键词检索够用 | lesson > 1000 条 |
| 抖音/微信公众号 | 先跑通小红书一个渠道 | 小红书稳定后 |

---

## 8. 成功指标

### Sprint 0 完成后 (1周)

- [ ] Scheduler 每个交易日自动产出 `market_regime_snapshots` 记录
- [ ] Agent 日报页面有真实数据 (不全是空)
- [ ] 非 panic 市场下，pipeline 至少能产出 ≥1 条 trade_signal

### Sprint 1 完成后 (3周)

- [ ] 每笔平仓 100% 有归因记录
- [ ] Strategist prompt 中包含 lesson 上下文
- [ ] 周日自动生成周复盘报告
- [ ] 前端可查看归因详情

### Sprint 2 完成后 (4周)

- [ ] 代码中只剩 LangGraph 一条管线
- [ ] `scripts/dev up` 一键跑通
- [ ] docs/ 下无冗余/过时文档

### Sprint 3 完成后 (6周)

- [ ] 每个交易日 ≥1 条内容发布到 SMA
- [ ] social_posts 表有 views/likes 指标
- [ ] 前端可看到 7 日互动趋势

---

## 9. 风险与缓解

| 风险 | 影响 | 缓解 |
|------|------|------|
| LLM 归因质量不稳定 | lesson 噪声大 | 加结构化约束 + 人工抽检前 20 条 |
| 归因样本太少 (bear 市) | 学习闭环空转 | S0.3 放宽规则 + mock 模式每日必跑 |
| SMA 服务不稳定 | 发布失败 | 重试 + 手动补发 |
| emappdata 接口变动 | 热点检测失效 | 双数据源 fallback + 监控告警 |
| 管线切换引入回归 | Scheduler 不跑了 | Sprint 0 先在本地验证一周 |

---

## 附录：文件变更清单

```
新增:
  src/agents/memory/__init__.py
  src/agents/memory/trade_attribution.py
  src/agents/social/__init__.py
  src/agents/social/content_strategy.py
  src/agents/social/metrics_collector.py
  scripts/daily_health_check.py

修改:
  scripts/scheduler.py              (管线切换 + 新 job)
  src/agents/cio/market_brain.py    (热点 fallback)
  src/agents/strategist/signal_generator.py  (lesson 注入)
  src/agents/executioner/executor.py (平仓触发归因)
  src/agents/reviewer/position_reviewer.py   (EXIT 触发归因)
  src/agents/influencer/content_engine.py    (SMA 实际发布)
  src/central_brain/metadata_store.py        (新表 + 新查询)
  src/feedback_loop/backtest_engine.py       (周复盘改造)
  src/webhook_listener/server.py             (新 API)
  config/trading_persona.yaml                (规则放宽)
  frontend/src/pages/AgentDailyReport.tsx    (归因 Tab)
  frontend/src/pages/ContentPipeline.tsx     (指标趋势)

删除 (Sprint 2):
  src/stock_analyzer/pipelines/daily_scan.py
  src/ai_platform/central_brain/workflow_engine/
  scripts/run_daily_workflow.py
  docs/operations.md
  docs/operations_manual.md
  docs/nas-deployment.md
  docs/runtime_overview.md
```
