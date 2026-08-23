# AI Closed Loop Lab — 架构重整与胜率提升设计

> 作者：总架构师
> 日期：2026-08-22
> 状态：Phase 0–3 代码已合入（待 NAS 部署）。Phase 2 回测过闸与 SIGNAL_FACTORY 默认仍关。
> 前置文档：`week4_30day_review.md`、`roadmap_to_60pct.md`、`roadmap_v2.md`、`project_status.md`、`next_phase_plan.md`
> 本文定位：把 2026-08-22 架构评审结论落成**可执行的设计 + 分阶段步骤**。不推倒重来，只修正理念与还清阻塞胜率的工程债。

---

## 0. 一句话结论

**LangGraph 管线骨架保留。** 成功率偏低不是因为缺 Agent，而是三件事没做成：

1. **执行链路不可靠**（调度、条件单、数据源、节点无错误边界）——直接把信号→成交打到 11.8%。
2. **决策理念反了**（LLM 自由生成信号，代码事后打补丁）——导致负期望策略反复出现、入场质量差。
3. **验证手段错了**（靠实时 mock 攒十几笔样本调 Prompt）——统计上永远到不了 60%。

架构债务（双 EventBus、多数据库、上帝对象、新旧管线并存）**不直接拉低胜率**，但会拖慢修上面三类问题的速度。必须分阶段还，不能和止血并行大重构。

---

## 1. 现状判定（以代码为准，不以文档为准）

### 1.1 已经写过、不要再当新需求开的

评审时对照代码核实，以下能力**已经落地**，本计划只验证、加固、监控，不重复实现：

| 能力 | 位置 | 状态 |
|------|------|------|
| 硬止损 / -8% 熔断（pre-LLM） | `src/agents/reviewer/position_reviewer.py` `_pre_llm_hard_check` / `_apply_rule_override` | 逻辑在，依赖复审真的每天跑 |
| 条件单触发 + 5 天过期 | `src/agents/reviewer/intraday_loop.py` `_check_pending_signals` / `_expire_stale_pending_signals` | 逻辑在，依赖盘中调度 + 真实行情 |
| 放量突破硬拦截 | `src/agents/strategist/signal_generator.py` `_BANNED_STRATEGY_KEYWORDS` L51-52, L729-736 | 代码拦截在，LLM 仍在浪费调用生成它 |
| 策略名规范化 | 同上 `_normalize_strategy_name` | 已有 |
| Scheduler 已切到 LangGraph | `scripts/scheduler.py` `job_daily_pipeline` → `run_daily_pipeline` | 已切；健康检查只有日志告警 |
| 盘中 30 分钟复审 + 5 分钟 regime drift | `scripts/scheduler.py` `setup_schedule` | 已注册；运维上是否持续存活未证实 |
| 热点板块 Sina / emappdata 兜底 | `akshare_client.py`、`emappdata_hot_sector.py` | 有兜底，质量未知 |
| 底部启动 / 主力吸筹策略 | Strategist prompt + 规则 | Prompt 已写，缺历史回测验证 |
| MiniQMT bridge | `scripts/miniQMT_bridge.py` | 代码有，影子账户未稳定跑 |

**执行纪律：任何阶段开工前，先跑 §3 Phase 0 审计清单，确认上述能力在 NAS 上实际生效。失效的修，生效的不要重写。**

### 1.2 真实数字（最近一次完整复盘，2026-06-15 ~ 07-31）

来源：`docs/week4_30day_review.md`

```
平仓 14 笔    胜率 35.7% (5W/9L)    净亏 ¥-22,906    盈亏比 0.37
信号 186  → 批准 49 (26%)  → 成交 22 (11.8%)
拒绝 116：regime 防守 51% / 仓位上限 43% / 重复持仓 6%
```

按策略：

| 策略 | 笔数 | 胜率 | P&L | 结论 |
|------|------|------|-----|------|
| 放量突破 | 9 | 11% | ¥-28,052 | 100% 净亏损来源；已禁但仍被 LLM 生成 |
| 价值投资 | 4 | 75% | +¥94 | 方向对，仓位与持仓周期过保守 |
| 热点板块前排回踩 | 1 | 100% | +¥5,052 | 唯一验证过的高期望组合，样本=1 |

### 1.3 问题分层

```
成功率偏低
├── A. 策略胜率 35.7%（理念）
│     ├── A1 LLM 当决策主体：自由生成策略名 / 入场价 / 仓位
│     ├── A2 约束在 Prompt 层，代码层只做事后拦截（浪费调用 + 漏网风险）
│     ├── A3 没有真正的历史回测，用 14 笔实时样本调参
│     └── A4 最佳策略（热点回踩）输入数据不稳，短线人格被禁后停摆
│
├── B. 运行成功率（工程）
│     ├── B1 条件单 68% 空转：依赖盘中复审 + 真实行情，调度/数据任一断就失效
│     ├── B2 LangGraph 节点无错误边界、无 checkpoint，一层挂则整日管线断
│     ├── B3 止损代码在，但复审间隙期出现过 -80% 单（000630）
│     ├── B4 NAS 上 eastmoney 被封，热点/板块数据质量不可观测
│     └── B5 健康检查只打日志，scheduler 挂了没人知道
│
└── C. 架构债务（间接）
      ├── C1 双 EventBus + 6+ 个 SQLite，反馈数据拼不齐
      ├── C2 DailyScanPipeline 与 LangGraph 并存，入口分裂
      ├── C3 MemoryStore 1500 行上帝对象
      └── C4 ai_platform/ 与主流程关系不清
```

**关键判断：A 不改，胜率上不去；B 不修，样本攒不齐、止损不可信；C 现在大动会拖死 A/B。**

---

## 2. 设计原则（本轮及以后所有改动必须遵守）

### P1. 约束下沉，LLM 上浮

```
错误：LLM 生成 TradeSignal → 代码打补丁（禁策略、改价格、砍仓位）
正确：规则/因子生成 CandidateSignal（含策略 ID、入场、止损、仓位上限）
      → LLM 只做 排序 / 否决 / rationale
      → RiskGovernor 只做组合级风控
```

LLM 不再有权发明策略名、发明远离现价的入场价。输出枚举必须是代码白名单。

### P2. 回测先行，线上后验

任何策略、规则权重、止损参数、人格改动：

1. 在 ≥2 年日线（必要时加分钟线）上跑出 **≥200 笔虚拟成交**；
2. 滚动胜率、盈亏比、最大回撤过闸；
3. 才能进 mock；mock 再满 30 笔且滚动胜率不崩，才谈 paper / live。

禁止再以「改完 Prompt，等下周看 3 笔成交」作为验证手段。

### P3. 硬规则不经过 LLM

止损、仓位上限、禁用策略、数据源不可用时禁开仓、pending 过期——全部是确定性代码。LLM 只能在规则未触发时提供判断。

### P4. 一条主路径，其余只读或删除

运行时只承认这一条写路径：

```
scheduler → LangGraph daily graph → central_brain.db
         ↘ reviewer / drift / attribution 回写同一库
```

`DailyScanPipeline`、`ai_platform` EventBus、独立 `strategy_metrics.sqlite` 不得再作为生产写路径。先冻结，再迁移，再删除。

### P5. 失败可见、失败可恢复

每个节点：捕获异常、写 `agent_events`、返回降级 state，不让异常冒泡炸掉整日管线。关键任务（复审、日管线、健康检查）失败必须告警，不能只 `logger.warning`。

### P6. 小步还债，不为重构而重构

C 类问题按「是否挡住 A/B」排序。EventBus 双写若导致反馈统计失真，优先统一读取层；拆 MemoryStore 排在胜率回升之后。

---

## 3. 目标架构

### 3.1 运行时（保留并加固）

```
                    ┌─ 告警通道（日志 + heartbeat 文件 + 可选 webhook）
                    │
scheduler (常驻, 受监控)
    │
    ├─ 09:35/11:35/14:00  MarketBrain snapshot
    ├─ 每 5 min           regime drift（已有）
    ├─ 每 5 min *新增*    条件单 + 硬止损扫描（从 30min 复审中拆出，无 LLM）
    ├─ 每 30 min          LLM 持仓复审（只处理未触发硬规则的仓）
    ├─ 14:50              stale position
    ├─ 15:05              收盘分析
    ├─ 15:15              归因回填
    ├─ 15:35              LangGraph daily pipeline（全人格串行）
    └─ 16:00              健康检查（失败必须告警）

LangGraph daily:
    MarketBrain → Explorer → SignalFactory(规则) → Strategist(LLM 否决/排序)
                → RiskGovernor → Executioner → Influencer
    每个节点包一层 node_guard：异常 → 降级 + 事件 + 不中断（或按节点策略中断）
```

盘中「看价格的事」和「想逻辑的事」必须拆开。价格扫描 5 分钟一次、零 LLM；LLM 复审维持 30 分钟。这是修 B1/B3 的关键结构变更。

### 3.2 决策权反转（A 类主改造）

```
Explorer.scan()
    └─ 产出 StockCandidate[]（现有）

SignalFactory   [新增, src/agents/strategist/signal_factory.py]
    输入: candidates + kline + fund + dragon_tiger + regime + persona.allowed_strategies
    对每只候选、每个白名单策略跑确定性检测器：
        hot_sector_pullback / bottom_reversal / institutional_accumulation
        / value_investing / defensive_bluechip / mean_reversion
    命中则产出 CandidateSignal:
        strategy_id, entry_price(=规则算出的触发价), stop_loss, target,
        position_pct_cap, entry_condition, evidence[]
    未命中 → 不进入 LLM

Strategist (改造, 不再自由生成)
    输入: CandidateSignal[]（最多 N 条，已带价格）
    LLM 只输出: {signal_id, decision: TAKE|PASS, rank, rationale, confidence}
    禁止字段: strategy, entry_price, stop_loss（即使模型写了也丢弃）
    硬拦截仍保留作第二道门

RiskGovernor (基本不动)
    组合级: regime posture / 仓位上限 / 相关持仓 / 重复票
```

白名单是唯一策略宇宙。Prompt 里不再列举「你可以发明的策略」，只解释如何在给定候选里做 TAKE/PASS。

### 3.3 回测实验室（A3 主改造）

现有 `feedback_loop/backtest_engine.py` 是**周复盘器**（对已发生信号拉事后 K 线），不是回测引擎。保留它，另建：

```
src/strategy_lab/
    detectors/          与 SignalFactory 共用的纯函数检测器
    data/               日线/分钟线本地缓存（parquet 或 sqlite）
    engine.py           向量化/逐日回放：信号 → 成交 → 止损止盈 → 绩效
    report.py           胜率 / PF / 最大回撤 / 分 regime 矩阵
    gates.py            上线闸门（见 §5）
    cli.py              python -m src.strategy_lab --strategy hot_sector_pullback --start 2024-01-01
```

**强制约束：SignalFactory 的检测器与回测检测器必须是同一份代码。** 禁止「回测一套规则、线上一套 Prompt」。

### 3.4 数据与事件（C 类，分两步）

**第一步（本轮必做）：统一读取面，冻结多余写路径。**

- 生产写入只允许 `data/central_brain.db`。
- `ai_platform` EventBus 停止订阅生产事件；`strategy_metrics.sqlite` 停止写入。
- 看板 / 周报 / Strategist 读经验，全部走 `experience_layer` → `central_brain.db`。

**第二步（胜率回升后再做）：拆 MemoryStore、删 ai_platform 死代码。**

目标模块边界：

```
src/central_brain/
    event_bus.py          从 metadata_store 拆出（仍进程内 + SQLite audit）
    store/
        session_repo.py
        signal_repo.py
        order_repo.py
        position_repo.py
        account_repo.py
        attribution_repo.py
    facade.py             get_central_brain() 保持兼容，内部转调 repo

src/agents/               运行时 Agent（唯一编排入口 src/graph/）
src/stock_analyzer/       数据源 + 规则检测器（被 Explorer / SignalFactory / strategy_lab 共用）
src/experience_layer/     读模型：ledger / memory / blacklist
src/feedback_loop/        周复盘 + prompt/规则建议（建议不再自动改权重）
src/strategy_lab/         离线回测
src/infra/

废弃（冻结 → 迁移引用 → 删除）：
    src/ai_platform/central_brain/          双 EventBus / 独立 workflow
    src/ai_platform/feedback_system/        与 experience_layer 重复
    src/stock_analyzer/pipelines/daily_scan.py  生产路径废弃，CLI 改调 LangGraph scan-only
```

`ai_platform/content_ai/` 若仍被 Influencer / SMA 使用，先迁到 `src/agents/influencer/` 或 `src/social_media_dispatcher/`，再删包。禁止两边同时加功能。

### 3.5 明确不在本轮做的

- 不新增多人格、不新接知识星球、不重做前端。
- 不把 EventBus 升级到 Redis。
- 不把 SQLite 迁 PostgreSQL。
- 不扩大实盘资金。影子账户（1 万）排在闸门通过之后。
- 不把「评论反哺选股」当胜率手段。

---

## 4. 成功标准

### 4.1 工程闸门（B 类，2 周内必须全绿）

| ID | 指标 | 达标 |
|----|------|------|
| G-ops-1 | scheduler heartbeat 文件连续 5 个交易日无 >10min 空洞 | 是 |
| G-ops-2 | 每个交易日 16:00 health check：`market_regime` 且 `pipeline_ran` | 5/5 |
| G-ops-3 | 日管线任一节点异常不得导致后续节点完全跳过且无事件记录 | 0 次静默失败 |
| G-ops-4 | 开仓当日及之后每个交易日，对该仓至少 1 次硬止损扫描（无 LLM） | 100% 持仓覆盖 |
| G-ops-5 | pending 信号：触发、过期或取消，7 日内必须离开 pending | 滞留 pending < 10% |
| G-ops-6 | `akshare_health.is_degraded==true` 时当日禁止新开仓 | 强制 |
| G-ops-7 | 热点板块列表日级非空，或明确标记 `hot_sectors_unavailable` 并禁用依赖它的策略 | 可观测 |

### 4.2 研究闸门（A 类，策略准入）

单个 `strategy_id` 允许进入 mock 的最低条件：

| ID | 指标 | 达标 |
|----|------|------|
| G-bt-1 | 回测区间 | ≥ 2024-01-01 至今，含至少一段明显调整 |
| G-bt-2 | 虚拟成交 | ≥ 200 笔 |
| G-bt-3 | 胜率 | ≥ 48%（短线）/ ≥ 55%（价值，按持有期定义） |
| G-bt-4 | Profit Factor | ≥ 1.2 |
| G-bt-5 | 最大回撤 | 短线 ≤ 15%，价值 ≤ 25% |
| G-bt-6 | 样本外 | 最近 60 个交易日独立一段，胜率不低于全样本 -8pct |
| G-bt-7 | 检测器同源 | `strategy_lab` 与 `SignalFactory` import 同一函数 |

未过闸的策略：`allowed_strategies` 里删除，Prompt 里删除，Factory 不产出。

### 4.3 线上胜率闸门（总目标）

分阶段看**已平仓、非 mock 滑点异常**的成交：

| 阶段 | 窗口 | 胜率 | 盈亏比 | 备注 |
|------|------|------|--------|------|
| 止血后 | 连续 20 笔 mock | ≥ 45% | ≥ 0.8 | 不含被禁策略残留单 |
| 校准后 | 连续 40 笔 mock | ≥ 52% | ≥ 1.0 | SignalFactory 上线后 |
| 影子盘 | 连续 40 笔 paper/影子 | ≥ 52% | ≥ 1.0 | 才允许讨论加资金 |
| 总目标 | 连续 100 笔 | ≥ 60% | ≥ 1.3 | 原 roadmap 目标，不在本轮承诺日期 |

**胜率分母只计已平仓。** 禁止用「未触发的 pending」或「被拒信号」美化数字。看板必须同时披露：信号数、批准数、成交数、平仓数、胜率、PF、最大单笔亏损。

---

## 5. 分阶段执行

时间按**工作日**估，串行主路径，允许 P0 内并行互不改同一文件的任务。

```
Phase 0   2 天     审计：哪些已生效、哪些是死代码
Phase 1   8 天     止血：调度 / 硬扫描 / 数据源 / 节点守卫
Phase 2  15 天     理念：SignalFactory + 回测实验室 + 策略过闸
Phase 3  10 天     还债：统一写路径、删双总线、收口入口
Phase 4    —      影子账户（闸门全绿后才开工，本文件只写准入条件）
```

---

### Phase 0 — 审计与冻结（2 天）

**目标：** 用 NAS 真实库和日志回答「代码在不在」vs「跑没跑」。产出一份 `docs/phase0_audit.md`（或本文件附录更新）。

#### 0.1 对照库表跑 SQL（半天）

在 NAS `central_brain.db` 上执行（本地可先对 `data/central_brain.db` 演练）：

```sql
-- 最近 40 个交易日漏斗
SELECT date(timestamp) d,
       COUNT(*) signals,
       SUM(status='filled') filled,
       SUM(status='pending') pending,
       SUM(status='expired') expired
FROM trade_signals
GROUP BY 1 ORDER BY 1 DESC LIMIT 40;

-- 被禁策略是否仍在入库
SELECT strategy, status, COUNT(*)
FROM trade_signals
WHERE timestamp >= date('now','-40 day')
  AND (strategy LIKE '%放量突破%' OR strategy_id='volume_breakout')
GROUP BY 1,2;

-- 硬止损是否真的执行过
SELECT date(created_at), COUNT(*)
FROM agent_events
WHERE event_type LIKE '%review%' OR payload LIKE '%stop_loss_triggered%'
GROUP BY 1 ORDER BY 1 DESC LIMIT 20;

-- 复审覆盖
SELECT date(created_at) d, COUNT(DISTINCT json_extract(payload,'$.symbol'))
FROM agent_events
WHERE agent='reviewer'
GROUP BY 1 ORDER BY 1 DESC LIMIT 20;

-- 热点板块是否为空
SELECT trade_date, regime, hot_sectors
FROM market_regime_snapshots
ORDER BY trade_date DESC LIMIT 20;
```

记录：最后一次 `job_daily_pipeline` / `job_intraday_review` 成功时间；heartbeat 空洞。

#### 0.2 对照进程与调度（半天）

- NAS 上 scheduler 容器 / 进程是否常驻，heartbeat 文件时间戳。
- 盘中 30 分钟任务是否真触发（`scheduler.log` 搜 `定时任务触发: 盘中复审`）。
- `allow_mock_fallback=False` 的条件单路径，行情失败是「跳过」还是「整天 0 触发」。

#### 0.3 冻结范围（当天做完）

在仓库发一则约定（PR / issue 即可）：

- 禁止向 `src/ai_platform/` 新增功能。
- 禁止新增 SQLite 文件。
- 禁止再往 `signal_generator.py` 的 Prompt 里加新策略名。
- 新策略只能加检测器函数。

**Phase 0 完成定义：** 审计记录进 git；冻结约定被接受；明确列出「已生效 / 代码有但没跑 / 完全缺失」三列。

**完成（2026-08-22）：** 见 [`docs/phase0_audit.md`](phase0_audit.md)。要点：真相库是 NAS `/volume1/docker/ai-lab/data/central_brain.db`（不是本机、也不是 `ai-lab-web`）；8 月交易日管线/复审在跑；**scheduler 已于 8/21 04:55 静默死亡**；放量突破 NAS 8 月已停但变体可绕过；真正尸堆是 147 条不过期的 `active`；AKShare 8/20 已 degraded 仍开仓。冻结写入仓库根 `AGENTS.md`。

---

### Phase 1 — 执行可靠性止血（8 天）

对应问题 B1–B5。做完后工程闸门 G-ops-* 必须可测量。

#### 1.1 拆出「无 LLM 的盘中硬扫描」  【P0 / 2 天】

**为什么：** 30 分钟 LLM 复审太贵、太慢、还受模型失败影响。000630 类事故的根因是「止损绑在复审上，复审没跑就等于没有止损」。条件单同理。

**做什么：**

新建 `src/agents/reviewer/hard_scan.py`：

```python
def run_hard_scan(persona_id: str | None = None) -> list[dict]:
    """每 5 分钟：硬止损 / 目标价减仓 / pending 触发 / 过期。零 LLM。"""
```

逻辑从现有代码抽，不要复制：

- 止损 / -8%：复用 `PositionReviewAgent._pre_llm_hard_check`（把该方法提成模块级函数 `evaluate_hard_exit(position, price)`）。
- pending / 过期：把 `intraday_loop._check_pending_signals` 和 `_expire_stale_pending_signals` 挪过来，`run_intraday_review` 改为调用 hard_scan，避免双跑。

`scripts/scheduler.py`：

- 新增 `job_hard_scan`，与 `job_regime_drift_check` 同一组 5 分钟槽（9:35–14:55，跳过午休）。
- `job_intraday_review` 保持 30 分钟，内部跳过已由 hard_scan 处理的 EXIT。

**验收：**

- 单测：价格跌破止损 → 必 EXIT，不 mock LLM。
- 单测：pending breakout 现价 >= entry → 调用 executor；>+30% 偏离不触发。
- 手工：停掉 LLM key，hard_scan 仍能平掉一根跌破止损的 mock 仓。

#### 1.2 节点守卫 + 日管线部分失败可见  【P0 / 1.5 天】

**文件：** `src/graph/workflow.py`、各 `run_*_node`。

```python
def node_guard(name: str):
    def deco(fn):
        def wrapped(state: TradingState) -> dict:
            try:
                return fn(state)
            except Exception as e:
                logger.exception("node %s failed", name)
                get_central_brain().log_agent_event(
                    state["session_id"], name, "node_failed",
                    {"error": str(e)},
                )
                # 按节点策略：market_brain/explorer 失败 → 结束开仓；
                # influencer 失败 → 忽略；其余 → 清空本层产出并结束
                return {"error": f"{name}:{e}", **_degrade(name, state)}
        return wrapped
    return deco
```

路由函数识别 `state["error"]` 时走 END，并保证 `persist_state` 仍执行。

**不要**上完整 LangGraph checkpoint（本轮过重）。先保证「挂了有记录、当日状态落库、不影响次日」。

**验收：** 单测里把 explorer 打成 raise，管线返回且 sessions 表有 state_json，events 有 `node_failed`。

#### 1.3 Scheduler 从「尽力而为」变成「受监控服务」  【P0 / 1.5 天】

已有 heartbeat 文件写入（`scripts/scheduler.py` ~L519）。补三件事：

1. **健康检查升级** `job_health_check`：
   - 已有 `market_regime` / `pipeline_ran` / akshare degraded。
   - 新增：今日 hard_scan 次数 ≥ 预期的 70%；今日若有 open position 则至少 1 次硬扫描；`hot_sectors` 空则打 `hot_sectors_unavailable`。
   - `all_ok == False` 时调用统一 `notify_ops(title, body)`。
2. **`notify_ops`** 新建 `src/infra/notify.py`：先写 `logs/ops_alerts.log` + 可选 webhook（env `OPS_WEBHOOK_URL`，没有就只落盘）。不要绑死某一家 IM。
3. **Docker / systemd**：在 `docs/operations.md` 或 `docs/nas-deployment.md` 加一段「heartbeat 超过 10 分钟视为死亡」的巡检命令。若已有 compose healthcheck，补上读 heartbeat。

**验收：** 故意停 scheduler 15 分钟，必须产生一条 ops alert；重启后 health check 能报出「今日管线未跑」。

#### 1.4 数据源降级策略改成「可交易 / 不可开仓」  【P0 / 2 天】

**现状：** Explorer 在 `is_mock` 时已跳过扫描（对）；但 Sina fallback 仍可能给出**没有板块**的真实行情，热点策略在空板块上装作成交。

**做什么：**

1. `MarketSnapshot` / regime snapshot 增加显式字段：
   - `data_quality: full | no_sector | degraded | mock`
   - `hot_sectors_source: eastmoney | emappdata | sina_infer | none`
2. `MarketBrain` 把该字段写入 `market_regime_snapshots`。
3. `SignalFactory`（Phase 2 上线前，先在现有 Strategist / Explorer 加闸）：
   - `mock` → 禁止开仓（已有）。
   - `no_sector` / `hot_sectors` 空 → 禁用 `hot_sector_pullback`，价值 / 底部启动仍可。
   - `degraded`（成功率 <50%）→ 当日禁止新开仓，只允许硬止损。
4. 给 `emappdata_hot_sector.py` 加探测单测 + 一次 NAS 实打实请求，记录是否仍通。不通则文档化替代（本地行业映射表 + 龙虎榜，不假装有热点）。

**验收：** 集成测试里把板块列表置空，断言不产生 `hot_sector_pullback` 信号；health check 能打印 `hot_sectors_source`。

#### 1.5 短线人格止血（不改理念，先停出血）  【P0 / 0.5 天】

- `config/trading_persona.yaml`：`short_term_hot_rotation_v1.allowed_strategies` 只留 `hot_sector_pullback`、`bottom_reversal`、`institutional_accumulation`。
- 确认 `forbidden_strategies` 含 `volume_breakout`。
- Strategist：当 LLM 输出不在 `persona.allowed_strategies` 时直接 None（现在只拦了关键词，变体「突破前高」已 map 到 volume_breakout，需确认 map 后再拦 `strategy_id`）。
- **Regime 为 bear/panic 时短线人格跳过 Strategist 调用**（RiskGovernor 现在会全拒，但 LLM 钱已经花了）。在 `run_strategy_node` 入口读 posture，defend/exit → 返回空信号。

**验收：** 用固定 fixture 跑 strategist，prompt 里塞「放量突破」也不得入库；bear 日该人格 `llm_calls=0`。

#### Phase 1 完成定义

- G-ops-1 ~ G-ops-7 连续 5 个交易日可统计（不足 5 日则 mock 时钟 + 日志回放证明机制成立）。
- 不再出现「有持仓但当日零硬扫描」。
- 放量突破 0 入库。
- 不承诺胜率数字在本阶段上升（样本仍少）；只承诺**不再以工程事故制造新的大亏单**。

---

### Phase 2 — 决策反转 + 回测实验室（15 天）

对应问题 A1–A4。这是胜率能不能从「止血」变成「可复现优势」的核心。

#### 2.1 抽出纯函数检测器  【3 天】

新建 `src/stock_analyzer/detectors/`（或 `src/strategy_lab/detectors/`，由 `stock_analyzer` 再导出，避免 agents 依赖 strategy_lab）：

| 文件 | 策略 | 输入 | 命中条件（初稿，回测可改参数，不可改接口） |
|------|------|------|------------------------------------------|
| `hot_sector_pullback.py` | 热点回踩 | kline, sector, hot_sectors | 板块在 topN；收盘距 20 日高点回撤 ∈ [3%, 12%]；量能 ≥ 20 日均量 × 0.8 |
| `bottom_reversal.py` | 底部启动 | kline | 近 N 日累计跌幅超阈；当日阳线且量 > 5 日均量；MA5 上穿 MA10 |
| `institutional_accumulation.py` | 主力吸筹 | dragon_tiger, kline | 机构净买超阈；换手放大；涨幅有限 |
| `value_investing.py` | 价值 | fundamentals, kline | 沿用现有 value_pool 规则，给出建议买点（回踩均线/不追 20 日新高） |
| `mean_reversion.py` | 均值回归 | kline | 短窗超卖 + 量能萎缩后首根放量 |

每个检测器：

```python
@dataclass(frozen=True)
class DetectorHit:
    strategy_id: str
    entry_price: float
    stop_loss: float
    target_price: float
    entry_condition: Literal["immediate", "pullback", "breakout"]
    position_pct_cap: float
    evidence: dict

def detect(ctx: BarContext) -> DetectorHit | None: ...
```

`entry_price` / `stop_loss` 全部由 K 线计算（例如止损 = 近 10 日低点或 ATR×2 的较宽者，且不超过 -8% 硬顶）。**禁止检测器返回「现价上方 15% 的突破价」作为当日 immediate 买入价。**

从 `src/agents/explorer/scanner.py`、`config/rules.yaml` 把已有规则迁过来，Scanner 改为调用同一套函数，避免第三份实现。

**验收：** 每个检测器至少 5 个 fixture（命中 / 不命中 / 边界）。禁止测 Prompt。

#### 2.2 SignalFactory 替换 Strategist 的生成权  【3 天】

新建 `src/agents/strategist/signal_factory.py`，`run_strategy_node` 改为：

1. Factory 对 `target_stocks` × persona.allowed_strategies 跑检测器，得到 `hits`。
2. 截断到 `MAX_LLM_ANALYSIS`（现 8）。
3. 调现有 LLM，但 **system prompt 重写** 为只评 `TAKE/PASS`，user 消息带上 `evidence` 和算好的价格。
4. 解析时只读 `decision/rank/rationale/confidence`；`TradeSignal` 的价格字段从 `DetectorHit` 拷贝。
5. 保留 banned / SUSPEND / entry sanity 作为死代码级第二道门（sanity 对 Factory 价格应几乎不再触发，触发则打 error 级日志——说明检测器有 bug）。

删除或大幅度缩短 `STRATEGIST_SYSTEM_PROMPT` 里的策略菜单和 5-Gate 打分（Gate 能下沉到检测器的就下沉；G5 时机匹配由 Factory 在 bear 日直接不产出短线 hit）。

**验收：**

- 单测：LLM mock 返回 `strategy=放量突破, entry_price=999`，最终信号仍是检测器的策略和价格。
- 单测：检测器全空时 **零次** LLM 调用。

#### 2.3 回测引擎  【5 天】

`src/strategy_lab/engine.py` 最小可用定义：

- 数据：日线优先（AKShare 批量拉 + 本地 parquet 缓存）。分钟线只给「条件单是否触发」的近似：用当日高/低判断 breakout/pullback 是否可能成交，无法判断则记 `fill_quality=optimistic`，报告里单独一列。
- 撮合假设（必须写进报告头，禁止默默改）：
  - immediate：次日开盘成交，滑点 0.2%；
  - pullback：信号日之后 5 日内最低价 ≤ entry 则按 entry 成交，否则过期；
  - breakout：5 日内最高价 ≥ entry 则按 entry×1.002 成交，否则过期；
  - 止损：成交后按日线最低价触及则当日收盘出（偏乐观，报告标注）；
  - 硬顶 -8%、目标价减半仓，与线上 `evaluate_hard_exit` 同一函数。
- 输出：每策略一份 json + markdown：n、胜率、PF、最大回撤、分年度、分 regime（regime 用当日或次日 MarketBrain 规则的离线简化版，先用均线/宽度近似，不调 LLM）。

`src/strategy_lab/gates.py` 实现 §4.2 布尔判断，CLI 退出码 0/1，供以后 CI 使用。

**数据获取注意：** NAS 拉两年全市场会爆。第一期只回测 **检测器宇宙**：用历史热点近似（涨幅榜 top 行业 / 本地缓存的板块成分），股票池限制在：

- 价值：现有 value_pool 成分的历史快照（没有快照就用当前池，报告注明前瞻偏差）；
- 短线：每日涨幅前 100 + 龙虎榜（有数据的日子）。

宁可样本定义窄、结论诚实，也不要「全 A 股随机抽」把期望做假。

**验收：**

- `python -m src.strategy_lab --strategy hot_sector_pullback --start 2024-01-01` 在可重复缓存下二次运行结果一致。
- 三份主策略（回踩 / 底部 / 价值）都有报告，过闸或明确不过闸。
- **不过闸的策略从 persona.yaml 移除。** 这是本阶段最重要的产品决策，不是可选附录。

#### 2.4 经验层用法修正  【2 天】

`experience_layer` 继续用，但改职责：

- `strategy_ledger` 的 SUSPEND 只作用于**已过闸且线上样本 ≥ 20** 的策略。样本 < 20 一律 `INSUFFICIENT`，**禁止**用 3～5 笔 mock 暂停一个回测过闸的策略。
- `meta_rule_synthesizer` 的输出改为「人工审阅的建议」，**停止自动回写** `rules.yaml` / `trading_persona.yaml`（`persona_updater.py` 改 dry-run 默认开）。自动改规则与「回测先行」冲突。
- `PromptEvolution.update_weights_from_records`：权重改到检测器参数必须走 strategy_lab 再过闸；本阶段关掉自动写权重，只留周报文本。

**验收：** 默认配置下跑周复盘，git status 不应出现 persona/rules 被改。

#### 2.5 漏斗与看板对齐  【2 天】

前端或 Streamlit（改现成的，不新做页面）：

- 漏斗：生成(Factory hits) → LLM TAKE → 风控批准 → 成交 → 平仓。
- 分策略 / 分人格 / 分 regime 胜率，标注样本量，n<20 显示「不足」。
- 数据源质量条：当日 `data_quality` / `hot_sectors_source`。

**验收：** 数字能对上 SQL，禁止再出现「186 信号 vs 22 成交」却无法按策略切开。

#### Phase 2 完成定义

- 线上新信号 100% 带有 `strategy_id ∈ 白名单` 且价格来自检测器。
- 至少 1 个短线 + 1 个价值策略过 G-bt-* 并保持 enabled。
- 未过闸策略已下线。
- mock 开始按新规则累积，进入「止血后 20 笔」观察窗（见 §4.3）。

---

### Phase 3 — 架构还债（10 天）

在 Phase 2 合并主干后开工，避免和 SignalFactory 抢同一批文件。

#### 3.1 宣判并拆除双写  【3 天】

1. 全局搜 `from src.ai_platform` / `from ai_platform` / `events.sqlite` / `strategy_metrics.sqlite`。
2. 仍被生产路径调用的：
   - `TopicGeneratorAgent` / `TradeContentAgent` → 迁到 `src/agents/influencer/` 或 dispatcher，改订 `central_brain.EventBus`。
   - `daily_market_workflow.py` → 删除或改成对 `run_daily_pipeline` 的薄封装。
3. `EventBus` 以 `src/central_brain/metadata_store.py` 的实现为准；`src/ai_platform/central_brain/event_bus/` 删除。
4. `scripts/run_daily_scan.py` 改为 `run_daily_pipeline("scan")`；`DailyScanPipeline` 标 `@deprecated`，保留一版只给测试迁完再删。

**验收：** 新产生的事件只出现在 `central_brain.db.events`；`data/event_bus/` 与 `data/strategy_feedback/` 不再增长。

#### 3.2 统一配置与魔法字符串  【2 天】

- 端口、第三方 URL 全部进 `src/infra/config.py` + `.env.example`，删散落默认 `localhost:8001/8003/8004/9090`。
- `entry_condition` / posture / regime 改 Enum 或 Literal 常量模块 `src/graph/enums.py`，workflow 路由不再写裸 `"exit"` 以外的隐式约定（可以保留字符串值，但比较走常量）。

#### 3.3 MemoryStore 按读/写边界切开（不做大搬家）  【4 天】

不要一次拆 12 个 repo。本轮只做：

```
metadata_store.py   保留连接、迁移、_conn
signal_repo.py      trade_signals
position_repo.py    positions / fills / orders
memory_store.py     其余方法暂留，内部转调
```

`get_central_brain().store` 属性保持兼容。目标是让 SignalFactory / 回测 / 复审不要再 import 1500 行模块里的私有 SQL。

**验收：** 现有 tests 全绿；没有业务功能变化。

#### 3.4 文档与入口收口  【1 天】

- `README.md` 去掉「v0.1 代码 / v0.2 文档未实现」的分裂描述，改为指向本文 + `system_architecture.md` 更新后的运行时图。
- `docs/project_status.md` 加一节「2026-08-22 重整」，把双管线从「已知债」改成「Phase 3 拆除中/已拆除」。
- `docs/next_phase_plan.md`、`docs/roadmap_v2.md` 头部加过期声明，避免再按旧 Sprint 开工。

#### Phase 3 完成定义

- 生产写路径唯一。
- 开发者从 README 能找到唯一日管线入口。
- 仓库不再出现第二套 EventBus 类。

---

### Phase 4 — 影子账户（只写准入，不写日历）

同时满足才允许 1 万元短线影子盘：

1. G-ops-* 连续 10 个交易日全绿；
2. 至少一个短线策略过 G-bt-*；
3. 新规则下 mock 连续 20 笔平仓胜率 ≥ 45% 且无单笔 < -10%（硬止损生效的证据）；
4. miniQMT 对账：订单、持仓、资金与 `accounts` 表一致，连续 5 日。

未满足时继续 mock。禁止用「接了实盘才知道行不行」跳过闸门。

---

## 6. 文件级任务清单

实施时按此开 PR，每个 PR 对应一行或一个小组，避免「重整大包」。

| ID | 阶段 | 任务 | 主文件 | 依赖 |
|----|------|------|--------|------|
| P0-0 | 0 | NAS/本地 SQL 审计文档 | `docs/phase0_audit.md`（新建） | — |
| P0-1 | 1 | `evaluate_hard_exit` 纯函数化 | `position_reviewer.py` | — |
| P0-2 | 1 | `hard_scan` + 5 分钟调度 | `hard_scan.py`（新）, `intraday_loop.py`, `scheduler.py` | P0-1 |
| P0-3 | 1 | `node_guard` | `workflow.py`, 各 `run_*_node` | — |
| P0-4 | 1 | `notify_ops` + health 升级 | `infra/notify.py`（新）, `scheduler.py` | — |
| P0-5 | 1 | `data_quality` 字段与开仓闸 | `akshare_client.py`, `market_brain.py`, scanner/strategist | — |
| P0-6 | 1 | 短线白名单 + bear 跳过 LLM | `trading_persona.yaml`, `signal_generator.py` | — |
| P1-1 | 2 | 检测器包 + fixture | `src/stock_analyzer/detectors/*` | P0-6 |
| P1-2 | 2 | Scanner 改调检测器 | `scanner.py`, `rules.yaml` | P1-1 |
| P1-3 | 2 | SignalFactory + prompt 缩权 | `signal_factory.py`（新）, `signal_generator.py`, `workflow.py` | P1-1 |
| P1-4 | 2 | strategy_lab 引擎 + CLI | `src/strategy_lab/*` | P1-1 |
| P1-5 | 2 | 三策略回测报告与是否过闸 | `reports/backtests/` | P1-4 |
| P1-6 | 2 | 下线未过闸策略 | `trading_persona.yaml` | P1-5 |
| P1-7 | 2 | 停用自动写规则/权重 | `persona_updater.py`, `prompt_evolution.py` | — |
| P1-8 | 2 | 漏斗看板 | dashboard / frontend 现有页 | P1-3 |
| P2-1 | 3 | 迁移/删除 ai_platform 总线 | `src/ai_platform/**`, influencer | P1-3 |
| P2-2 | 3 | `run_daily_scan` 改道 | `scripts/run_daily_scan.py` | P2-1 |
| P2-3 | 3 | 配置与枚举收口 | `infra/config.py`, `graph/enums.py` | — |
| P2-4 | 3 | store 小拆分 | `central_brain/` | — |
| P2-5 | 3 | README / status / 旧 roadmap 过期声明 | `docs/*`, `README.md` | P2-1 |

---

## 7. 测试与发布策略

### 7.1 必补测试（按阶段门禁）

- **Phase 1：** `tests/test_hard_scan.py`、`tests/test_node_guard.py`、`tests/test_banned_strategy_id.py`、`tests/test_data_quality_gate.py`。
- **Phase 2：** 每检测器 fixture；`tests/test_signal_factory_ignores_llm_prices.py`；`tests/test_strategy_lab_deterministic.py`（用 2～3 只股票的固定 parquet）。
- **Phase 3：** 现有测试全绿即可，另加「EventBus 只有一个 class 定义」的静态断言（grep 测试）。

现有 e2e 不稳定就不要当门禁；门禁用新的、快的、不访问外网的单测。访问行情的测标记 `@pytest.mark.network`，CI 默认跳过。

### 7.2 发布

- NAS 继续单镜像。Phase 1 改的是 scheduler 行为，需要**滚动前确认 heartbeat 与回滚命令**写进 operations。
- 数据库：`data_quality` / `hot_sectors_source` 走现有 `_schema_migrations`，禁止手改 NAS 库。
- Feature flag：`SIGNAL_FACTORY=1`（默认 Phase 2 合并后开；出问题可关回旧 Strategist，旧路径只保留两周）。

---

## 8. 风险与待决问题

| 风险 | 影响 | 缓解 |
|------|------|------|
| 日线回测高估条件单成交 | 过闸策略上线后胜率回落 | 报告标 `fill_quality`；线上 20 笔观察窗；必要时先只发 immediate |
| emappdata 也挂 | 热点策略无输入 | 禁用该策略，不编造热点；价值线独立存活 |
| 检测器过严导致 0 信号 | 又进入「无样本」 | 回测同时输出命中率；命中率 < 每周 2 笔则放宽参数再回测，不靠 LLM 补信号 |
| 5 分钟 hard_scan + 多人格把行情源打爆 | 扫描失败=止损失灵 | 价格本地短缓存 30～60s；失败 `notify_ops` |
| Phase 2 与主线并行冲突 | 回并成本 | SignalFactory 开关；检测器先合、Strategist 后切 |
| 把「胜率」做成目标函数去拟合回测 | 过拟合 | 样本外 60 日硬闸 + 禁止为过闸调 10 个参数而不登记 |

**需要你拍板的两件事（开工 Phase 2 前）：**

1. 价值人格的胜率定义：按「平仓盈利」还是「持有 60 日浮动」（后者更合理，但和短线数字不可比，看板要分开）。
2. 回测不过闸的策略是「立刻从线上删除」还是「仅 mock 观察」——本文默认**立刻删除**，与 30 天复盘「放量突破拖死账户」的教训一致。

---

## 9. 与旧文档的关系

| 旧文档 | 关系 |
|--------|------|
| `roadmap_to_60pct.md` | 阶段 1/2 清单多项已勾选，但胜率未达；目标 60%/100 笔保留，路径改为本文 |
| `roadmap_v2.md` | 90 天接实盘过于乐观；本文 Phase 4 无日历 |
| `next_phase_plan.md` | 「先跑起来攒数据」在 5 月正确，现在数据已证明错误策略在跑；改为先停错误策略 + 回测 |
| `cognitive_agent_design.md` | 长期愿景仍有效；本轮明确 **Cognitive ≠ LLM 自由决策** |
| `project_status.md` | Phase 3 完成后更新完成度与技术债列表 |
| `week4_30day_review.md` | 问题清单的事实来源，本文不重复展开案例 |

---

## 10. 建议的开工顺序（本周）

若只投入一人：

1. Day 1–2：Phase 0 审计（否则后续全是猜测）。
2. Day 3–4：P0-1 + P0-2（hard_scan）——立刻降低「再出一笔 -80%」的概率。
3. Day 5：P0-6（短线白名单 + bear 不烧 LLM）。
4. 次周：P0-3 / P0-4 / P0-5，然后进入检测器，不要先拆 EventBus。

两人则可并行：A 做 P0-1/2/3，B 做 P0-4/5/6。

---

**本文不承诺 60% 胜率的日期。** 承诺的是：工程事故不再制造爆仓样本；策略对错由同源回测决定；LLM 不再拥有发明交易规则的权力。这三件事落地后，60% 才是一个可以讨论的统计问题，而不是一个 Prompt 问题。
