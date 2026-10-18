# AI Closed Loop Lab — 事件 × 决策转盘（Decision Wheel）架构设计与实施计划

> 日期：2026-10-17
> 状态：设计稿（待评审）
> 关联文档：`AGENTS.md`、`architecture_refactor_and_winrate_plan.md`、`runtime_overview.md`、`event_catalog.md`
> 适用范围：NAS 生产执行引擎（`/volume1/docker/ai-lab/`，运营真相 `central_brain.db`）

---

## 1. 背景

### 1.1 现状结构

当前系统由 `scripts/scheduler.py` 按时钟触发 20 余个互相独立的 job，job 之间不直接调用，通过 `central_brain.db` 的表接力，所有成交最终经过 `ExecutionEngine`：

```
scheduler(时钟) ─▶ job ─▶ 决策(LangGraph / 规则 / LLM) ─▶ ExecutionEngine ─▶ central_brain.db
```

| 动作 | 现有实现 | 触发 |
|---|---|---|
| 日常选股+开仓 | `src/graph/workflow.py`（market_brain→explorer→strategist→risk_governor→executioner） | 15:35 |
| 条件单触发 | `intraday_loop._check_pending_signals`（挂在 hard_scan 里） | 每 5 分钟 |
| 规则出场 | `hard_scan.run_hard_scan` + `hard_rules.evaluate_hard_exit` + `stop_confirm` | 每 5 分钟 |
| LLM 出场 | `intraday_loop.run_intraday_review` + `position_reviewer` | 每 30 分钟 |
| 隔夜客入场 | `tail_hunt.execute_tail_hunt` | 14:35 |
| 隔夜客早盘 | `tail_hunt.execute_morning_pulse`（委托 hard_scan） | 09:35 一次 |
| 隔夜客强平 | `tail_hunt.execute_force_close_ultra_short` | 14:30 |
| 出场公共入口 | `intraday_loop._execute_review_action` | — |

### 1.2 暴露的问题（均有生产证据）

| # | 问题 | 证据 |
|---|---|---|
| P1 | **同一持仓有多个出场方，互不知情**，每类冲突都要在某条路径上单独打补丁 | 9 月脉冲与 hard_scan 双轨；10 月隔夜客仍被 LLM 复审；10-13 LLM 绕过止损二次确认卖出 301697 |
| P2 | **角色不是一等公民**，特殊规则散落 | `ultra_short_momentum_v1` 在 7 个文件硬编码 15 处；价值股靠 persona_id 子串判断 |
| P3 | **"何时触发"与"做什么"耦合在 job 里**，无法表达角色自定义的后续观察 | 早盘脉冲只能 09:35 跑一次，无法"1 分钟后再看" |
| P4 | **同一规则多份实现** | 止损分布在 hard_rules / position_reviewer / stop_confirm；底部反转曾有检测器、scanner、Prompt 三份定义 |
| P5 | **隐式时钟依赖、断链无人发现** | `emergency_review_requested` 只写不读；09:30 复审拿不到当日数据（002285） |
| P6 | **线上与回测语义不同源** | 54 笔买单中 28 笔在 15:3x 按收盘价成交，回测为次日开盘价 |
| P7 | **状态写入不收口** | 16 个文件直接 `brain.store._conn()` 写 SQL；止损确认状态在进程内存 |

### 1.3 设计目标

1. **"何时"与"做什么"分离**：事件层定义触发；转盘定义一个业务动作的具体逻辑。
2. **角色由配置描述**：角色 = 事件绑定 + 转盘参数；新增角色只改配置。
3. **依据可插拔、互不调用**：所有依据通过统一协议被转盘串联。
4. **系统底线与单一出场出口不可配置、不可绕过**。
5. **线上与回测同源**：同一转盘可在历史数据上回放。
6. **决策全程可追溯**：每次转盘运行留下完整轨迹，监控页直接展示。
7. **角色 = 身份 + 行为配置 + 记忆**：在转盘外加载为只读档案；记忆按角色隔离；规则修改必须经审批。

### 1.4 非目标

- 不引入消息队列、Redis、新 SQLite 文件、分布式调度。
- 不在 `src/ai_platform/` 下新增任何东西（AGENTS.md 冻结项）。
- 不改变 `ExecutionEngine` 的撮合与硬闸门职责。
- 不在本设计内调整任何策略参数或研究闸门阈值。

---

## 2. 总体架构

```
┌ ① 事件层：什么时候进来 ─────────────────────────────────────────────┐
│ 预定义事件：clock.0935 / clock.1430 / clock.1435 / clock.1535 …        │
│            market.regime_drift / account.daily_loss_breach …         │
│ 自定义事件：转盘运行时产生的后续事件（follow-up）                        │
│            "1 分钟后复查 688008，最多 10 次，14:30 前有效"               │
│ 存储：central_brain.db.wheel_events；Dispatcher 到点取出分发             │
└─────────────────────────────────────────────────────────────────────┘
        ▼ (event, persona)
┌ ② 角色绑定：这个角色在这个事件上跑哪个转盘、带什么参数 ─────────────────┐
│ config/wheels.yaml → personas.<id>.bindings                          │
└─────────────────────────────────────────────────────────────────────┘
        ▼
┌ ③ 转盘：一个业务动作的具体步骤（按配置顺序执行，确定性） ─────────────────┐
│ 取数步骤 DataStep  → 写入本次运行的 Context（带缓存）                    │
│ 判断步骤 Factor    → 只读 Context，输出 Opinion                         │
│ 合并规则            → funnel（漏斗）或 decision（否决/提议/裁决/修饰）      │
│ 输出               → 状态变更（候选/信号/决策）＋ 可选后续事件              │
└─────────────────────────────────────────────────────────────────────┘
        ▼ Decision
┌ ④ 系统底线 ＋ 单一出口（任何配置都不能绕过） ───────────────────────────┐
│ SystemFloor：交易日历、交易时段、数据不可用禁开仓、T+1、整手、价格异常      │
│ ExitGateway：同一持仓同一时刻只允许一个出场决策生效（去重/锁/限频）         │
│ ExecutionEngine：现有撮合与硬闸门（不变）                                │
└─────────────────────────────────────────────────────────────────────┘
        ▼
   central_brain.db（唯一真相）＋ events（决策轨迹）
```

转盘之间**不直接调用**，通过数据库接力（筛查转盘写候选 → 买点确认转盘读候选），与"数据库状态为唯一依据"一致。

---

## 3. 事件层

### 3.1 事件模型

```python
@dataclass(frozen=True)
class WheelEvent:
    event_id: str                 # 幂等键
    type: str                     # clock.0935 / followup.morning_watch / market.regime_drift …
    due_at: datetime              # 计划触发时间
    persona_id: str | None        # None = 对所有绑定了该事件的角色广播
    subject: dict                 # {"symbol": "688008"} / {"position_id": "..."} / {"account": "..."}
    payload: dict                 # 事件附带参数（如后续事件的观察基准价）
    source: str                   # scheduler / wheel:<name> / drift_detector / account_monitor
    parent_event_id: str | None   # 后续事件指向产生它的事件
    attempt: int                  # 第几次复查（follow-up 计数）
    max_attempts: int
    expires_at: datetime | None
```

### 3.2 事件来源

| 类别 | 例子 | 来源 |
|---|---|---|
| 时钟事件 | `clock.0935`、`clock.intraday_5m`、`clock.1430`、`clock.1435`、`clock.1535` | scheduler 只负责按时写入 `wheel_events` |
| 市场事件 | `market.regime_drift`、`market.regime_panic` | `regime_drift` 检测器写入（修复 P5 断链） |
| 持仓事件 | `position.opened`、`position.stop_breached` | 转盘输出或 ExecutionEngine 成交后写入 |
| 账户事件 | `account.daily_loss_breach`、`account.consecutive_losses` | 账户监控步骤写入 |
| 后续事件 | `followup.morning_watch` | 转盘运行时产生（见 3.3） |

所有事件必须先落库再分发；scheduler 不再直接执行业务逻辑。

### 3.3 后续事件（角色自定义观察）

转盘可输出 `FollowUp`，由 Dispatcher 落库为新事件：

```python
@dataclass(frozen=True)
class FollowUp:
    type: str              # followup.morning_watch
    after: timedelta       # 1 分钟后
    subject: dict          # {"position_id": ...}
    payload: dict          # 观察基准，如 {"ref_price": 207.6, "high": 208.1}
    max_attempts: int      # 最多复查次数
    until: time            # 最晚有效时间（如 14:30）
```

强制约束（Dispatcher 统一执行，转盘无需关心）：

1. **只在交易日、交易时段内触发**；跨午休自动顺延到 13:00，超过 `until` 作废。
2. **attempt ≥ max_attempts 作废**，防止无限复查。
3. **关联对象失效即作废**：持仓已平、信号已过期时，相关后续事件标记 `cancelled`。
4. **幂等**：`event_id = hash(type, subject, due_at 分钟)`，重复登记只保留一条。
5. 每个持仓同类后续事件同时最多 1 条在途。

### 3.4 Dispatcher

- 运行在 `ai-lab-scheduler` 进程内，每 15 秒轮询 `wheel_events WHERE status='due' AND due_at <= now`。
- 取出后置 `running`，按 `(event, persona)` 查绑定，运行转盘，结束置 `done` / `failed` / `cancelled`。
- 单事件执行超时（默认 60 秒；含 LLM 的转盘 180 秒）置 `failed` 并 `notify_ops`。
- 进程重启时将 `running` 超过超时时间的事件回退为 `due`（转盘需幂等，见 5.5）。

---

## 4. 角色绑定

角色不再在代码里写 if 判断，全部由绑定描述：

```yaml
personas:
  ultra_short_momentum_v1:
    floor_overrides: {}                     # 系统底线不可覆盖，此处只为显式声明
    bindings:
      clock.0935:            { wheel: morning_watch }
      followup.morning_watch: { wheel: morning_watch }
      clock.intraday_5m:     { wheel: exit_confirm, params: { stop_pct: 2.5, take_profit_pct: 3.5 } }
      clock.1430:            { wheel: overnight_force_close }
      clock.1435:            { chain: [market_screen_ultra, entry_confirm_ultra] }
      clock.1530:            { wheel: watchlist_maintain_ultra }

  short_term_hot_rotation_v1:
    bindings:
      clock.intraday_5m:     { wheel: exit_confirm, params: { stop_confirm: 2, max_loss_pct: 8 } }
      clock.intraday_30m:    { wheel: llm_position_review, params: { mode: advisory } }
      clock.1535:            { chain: [market_screen, entry_confirm] }
      clock.next_open:       { wheel: order_fill_at_open }

  warren_buffett_v1:
    bindings:
      clock.intraday_5m:     { wheel: exit_confirm, params: { stop_confirm: 2, max_loss_pct: null } }
      clock.intraday_30m:    { wheel: llm_position_review, params: { mode: advisory, value_noise_filter: true } }
      clock.1535:            { chain: [market_screen_value, entry_confirm_value] }
```

- `chain`：同一事件内依次运行多个转盘，前一个的输出落库后后一个读取（仍是数据库接力，不传内存对象）。
- 没绑定的事件，该角色就不会响应。例如隔夜客不绑定 `clock.intraday_30m`，LLM 复审对它在结构上就不存在。
- 价值股判断、隔夜客白名单等全部改为读绑定与参数，删除硬编码 persona_id。

---

## 5. 转盘协议

### 5.1 转盘定义

```yaml
wheels:
  exit_confirm:
    kind: decision
    data:    [positions_open, quote_realtime]
    veto:    [floor.t_plus_1, floor.real_quote_required]
    propose: [rule.stop_loss, rule.max_loss, rule.target_reduce, rule.narrow_stop, rule.pulse_take_profit]
    resolve: priority
    modify:  [mod.stop_confirm, mod.reduce_daily_cap]
    output:  exit_proposal            # 交给 ExitGateway

  market_screen:
    kind: funnel
    data:    [market_quotes, sector_board, market_volume, volume_7d]
    steps:   [screen.hot_sectors(top=5), screen.hot_stocks(per_sector=4),
              screen.liquidity(min_amount=3e8), screen.experience_blacklist]
    output:  candidates
```

配置只允许：**选哪些步骤、什么顺序、填什么参数**。不允许条件表达式；需要逻辑就写成新的 Factor 并配测试。

### 5.2 两类步骤

```python
class DataStep(Protocol):
    name: str
    provides: tuple[str, ...]                  # 写入 Context 的键
    def load(self, ctx: Context, params: dict) -> dict: ...

class Factor(Protocol):
    name: str
    stage: Literal["veto", "propose", "modify", "screen"]
    requires: tuple[str, ...]                  # 依赖的 Context 键，缺失则该步骤 skip 并记录
    def evaluate(self, ctx: Context, params: dict) -> Opinion: ...
```

- **DataStep** 负责取数，结果按 `(provider, key, 分钟)` 缓存在同一轮 Dispatcher 内，多个转盘不重复拉行情。回测时替换为历史数据源实现。
- **Factor** 是纯函数：只读 Context，不发网络请求，不直接写库，不调用其他 Factor。需要持久状态时通过 `Opinion.state_update` 返回，由转盘统一写入 `wheel_factor_state`。

### 5.3 Context

```python
@dataclass(frozen=True)
class Context:
    event: WheelEvent
    persona: PersonaPolicy        # 绑定参数 + 角色元数据（kind=short/ultra/value…）
    now: datetime                 # 回测时为模拟时间
    data: Mapping[str, Any]       # DataStep 产出
    state: Mapping[str, Any]      # 该 Factor 在 wheel_factor_state 中的历史状态
```

### 5.4 Opinion 与合并规则

```python
@dataclass(frozen=True)
class Opinion:
    factor: str
    verdict: Literal["pass", "veto", "propose", "modify", "skip"]
    action: Literal["BUY", "HOLD", "ADD", "REDUCE", "EXIT", "WATCH"] | None = None
    qty_pct: float | None = None
    priority: int = 100           # 越小越优先，仅 propose 使用
    reason: str = ""
    evidence: dict = field(default_factory=dict)
    state_update: dict | None = None
    follow_up: FollowUp | None = None
```

**decision 类转盘（每个 subject 独立执行）**

```
① veto    任一 veto → 结论 HOLD/SKIP，停止
② propose 收集全部提议
③ resolve priority：取 priority 最小的提议；同级时 EXIT > REDUCE > HOLD（卖方保守）
            LLM 类 Factor 固定 priority ≥ 500，只能在无规则提议时生效（advisory）
④ modify  依次修饰结论：止损确认、REDUCE 每日上限、仓位上限、价值股降噪……
            modify 只能把动作调"弱"（EXIT→REDUCE/HOLD、减小数量），不能调"强"
```

不采用加权投票：权重会诱导针对回测调参，且无法说清是哪条依据导致结论。

**funnel 类转盘**

```
data → screen₁ → screen₂ → … → 排序取前 N → 写入 candidates
每个 screen Factor 输出保留/淘汰及原因；淘汰原因全部进入轨迹
```

### 5.5 转盘输出与幂等

```python
@dataclass(frozen=True)
class WheelResult:
    wheel: str
    decisions: list[Decision]          # decision 类
    candidates: list[dict]             # funnel 类
    follow_ups: list[FollowUp]
    trace: list[Opinion]               # 完整轨迹
```

- 写库以 `(event_id, wheel, subject)` 为幂等键：重复运行同一事件不会重复下单或重复写候选。
- 所有 `trace` 写入 `events`（`event_type=wheel_completed`），监控页按事件展开即可看到"为什么卖 / 为什么没买"。

---

## 6. 系统底线与单一出场出口

### 6.1 SystemFloor（不可配置）

| 规则 | 现有位置 | 归属 |
|---|---|---|
| 交易日历、交易时段 | `src/infra/trading_calendar.py` | Dispatcher + ExecutionEngine 双重校验 |
| 数据 mock/degraded/unavailable 禁开仓 | `data_quality`、workflow 路由 | 所有 `BUY` 决策前校验 |
| T+1、整手、无持仓禁卖、价格异常 | `ExecutionEngine._mock_execute` | 不变 |
| 卖出必须有真实价 | `_execute_review_action` | 移入 ExitGateway |
| 隔夜客只能开 `ultra_short_momentum` | `ExecutionEngine` 白名单 | 保留，改为读 PersonaPolicy.allowed_strategies |

### 6.2 ExitGateway（统一出场出口）

所有卖出类决策（来自任何转盘）必须经过：

1. **持仓锁**：同一 `position_id` 同时只允许一个出场决策执行；其余决策记录 `skipped: exit_in_progress`。
2. **同一事件窗口去重**：同一分钟内对同一持仓的多个出场决策合并为最强的一个（EXIT 优先）。
3. **REDUCE 每持仓每交易日最多 1 次**（从 `_sold_today` 迁入）。
4. **真实价校验**、T+1 前置校验（ExecutionEngine 仍做兜底）。
5. 执行后更新 `positions`、取消该持仓所有在途后续事件。

`intraday_loop._execute_review_action` 在 Phase 1 完成后只保留为 ExitGateway 的薄包装，最终删除。

---

## 7. 角色档案（Persona Profile）

### 7.1 结论

- **角色本身不是转盘**，是转盘读写的状态。角色档案在**转盘外**加载：事件进来时一次性装配成只读快照，放进 `Context.persona`，转盘内所有步骤只读。
- **形成和更新角色的过程是转盘**：归因、观察池维护、复盘总结，都是由事件触发的"记忆类转盘"。

### 7.2 三层模型

| 层 | 内容 | 谁来改 | 变化频率 | 存储 |
|---|---|---|---|---|
| ① 身份 Identity | 投资哲学、风格、风险偏好、持仓周期、资金规模、社交口吻 | **只能由人改** | 很少 | `config/trading_persona.yaml` |
| ② 行为配置 Policy | 事件绑定、转盘参数、允许策略、是否接受 LLM 意见 | **人审批后改**（可由提议发起，见 7.6） | 偶尔 | `config/wheels.yaml` 中该角色段落 |
| ③ 记忆 Memory | 历史交易与归因、教训、总结的经验、观察中的股票、冷却与黑名单、Factor 状态 | **系统自动写** | 持续 | `central_brain.db` |

身份决定"他是谁"，行为配置决定"他怎么做"，记忆决定"他经历过什么、现在盯着什么"。

### 7.3 加载协议

```python
@dataclass(frozen=True)
class PersonaProfile:
    persona_id: str
    identity: Identity            # 来自 trading_persona.yaml（只读）
    policy: PersonaPolicy         # 来自 wheels.yaml（只读）
    memory: MemoryViews           # 按 as_of 截取的记忆视图（惰性加载、只读）
    as_of: datetime               # = Context.now；回测时为模拟时间
```

规则：

1. **加载时机**：Dispatcher 在分发 `(event, persona)` 时装配一次，同一事件下的 chain 中多个转盘共用同一份档案。
2. **只读**：转盘与 Factor 不得修改档案；记忆变更只能通过记忆类转盘的输出写库（见 7.5）。
3. **按时刻截取（as_of）**：所有记忆视图只包含 `as_of` 之前的数据，回测回放历史某日时不得看到未来信息。
4. **惰性加载**：记忆视图按转盘声明的依赖按需查询，未声明的不加载。
5. **配置变更生效时机**：身份与行为配置在交易时段内不热更新，只在 scheduler 重启或 15:00 后的配置重载点生效，避免盘中行为突变。

### 7.4 记忆视图（以 DataStep 形式提供）

| 视图 | 内容 | 现有来源 | 隔离 |
|---|---|---|---|
| `persona.trade_history(days)` | 近期开平仓、盈亏、退出原因 | `positions`、`fills` | 按角色 |
| `persona.attributions(days)` | 平仓归因 | `trade_attributions`（经 position_id 关联角色） | 按角色 |
| `persona.lessons(top)` | 按相关度排序的教训 | `lessons`（经 attribution 关联角色） | 按角色 |
| `persona.stock_memory(symbol)` | 该角色在该股票上的历史与冷却 | `experience_layer.StockMemory` | **按角色**（现状为全局，需改造） |
| `persona.strategy_stats` | 该角色各策略的近期表现 | `experience_layer.StrategyLedger` | **按角色**（现状为全局，需改造） |
| `persona.blacklist` | 动态黑名单 | `experience_layer.DynamicBlacklist` | 按角色（已支持） |
| `persona.watchlist` | 观察中的股票 | `watchlist` | **按角色**（现状无 persona 字段，需改造） |
| `persona.factor_state` | 止损确认等 Factor 状态 | `wheel_factor_state` | 按角色 |
| `shared.stock_flags(symbol)` | 跨角色共享的事实（财务造假、退市风险、重大违规） | 新增 | 全局共享 |

**记忆默认按角色隔离**。现状问题：`StockMemory` 与 `StrategyLedger` 不区分角色，短线角色在某股票上的止损会让价值角色也进入该股冷却；`watchlist` 无 persona 字段，defend 日任何角色存入的信号，次日都可能被其他角色的 strategist 读到。只有客观事实（7.4 末行）全局共享。

转盘中的使用方式：

- **规则 Factor** 读 `policy.params`：同一个 `rule.stop_loss`，被不同角色调用表现为不同手法。
- **记忆**由转盘在 `data:` 中声明依赖，判断步骤只读 Context。
- **LLM Factor** 的提示词由 `identity` + 声明的记忆视图拼成；角色"像不像巴菲特"由输入决定，不写死在代码话术里。

### 7.5 记忆类转盘

| 转盘 | 触发事件 | 写入 | 自动 |
|---|---|---|---|
| `attribution` | `position.closed` | `trade_attributions`、`lessons`、`persona.stock_memory` 冷却 | 是 |
| `watchlist_maintain` | `clock.1530`（各角色可绑定不同参数） | `watchlist`（按角色） | 是 |
| `daily_journal` | `clock.close` | 当日决策摘要（复用 `closing_analysis`） | 是 |
| `weekly_reflection` | `clock.weekly` | 教训归纳 + **规则修改提议** `persona_proposals` | 只写提议 |

记忆类转盘与决策类转盘使用同一套协议（DataStep / Factor / 轨迹），区别只在输出写入记忆表而不是产生交易决策。

### 7.6 经验与规则：提议 → 闸门 → 审批

"总结的策略"拆为两类，处理方式不同：

| | 经验/教训（记忆） | 规则修改（行为配置） |
|---|---|---|
| 例子 | "600809 曾因盘中放量下跌被误卖""本周热点轮动过快" | "止损 5%→3%""停用 hot_sector_pullback""隔夜客加入 morning_watch" |
| 生效方式 | 自动写入记忆，供 LLM 参考或作为冷却/黑名单软过滤 | 只生成提议；需通过回测闸门（含随机买点对照）并经人工批准后改配置 |
| 能否自动 | 可以 | **不可以** |

提议通道：

```
weekly_reflection ─▶ persona_proposals(status=proposed)
                       │ 附：依据的交易样本、预期影响
                       ▼
                 strategy_lab 自动回测（参数变更类） ─▶ status=backtested + 报告
                       ▼
                 页面人工审批 ─▶ approved：生成 wheels.yaml 变更（git 提交）
                               rejected：记录原因，进入记忆避免重复提议
```

- 身份层（①）不接受任何自动提议，只能人工修改。
- 现有 `experience_layer.persona_updater`（`PERSONA_AUTO_UPDATE` 开关）与 `meta_rule_synthesizer` 迁移为提议的生产者，不再直接回写配置。
- 少于研究闸门样本要求的提议（如基于十几笔交易）只能标记为"观察"，不进入审批。

### 7.7 配置示例

```yaml
# config/trading_persona.yaml —— 身份（人写）
warren_buffett_v1:
  identity:
    name: 巴菲特
    philosophy: [买好公司并长期持有, 别人恐惧时贪婪]
    holding_period: months
    risk_appetite: low
    capital: 300000

# config/wheels.yaml —— 行为配置（人审批）
personas:
  warren_buffett_v1:
    policy:
      kind: value
      allowed_strategies: [value_investing]
      params: { stop_pct: 25, llm_mode: advisory, value_noise_filter: true }
      memory_views: [trade_history(days=180), lessons(top=10), stock_memory, watchlist]
    bindings: { ... }     # 见 §4
```

`kind: value` 取代当前通过 persona_id 子串（duan/buffett/value）判断价值股的做法。

## 8. 数据模型（均在 `central_brain.db` 内，经 MemoryStore 迁移）

```sql
CREATE TABLE IF NOT EXISTS wheel_events (
    event_id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    persona_id TEXT,
    subject_json TEXT NOT NULL DEFAULT '{}',
    payload_json TEXT NOT NULL DEFAULT '{}',
    source TEXT NOT NULL,
    parent_event_id TEXT,
    attempt INTEGER NOT NULL DEFAULT 1,
    max_attempts INTEGER NOT NULL DEFAULT 1,
    due_at TEXT NOT NULL,
    expires_at TEXT,
    status TEXT NOT NULL DEFAULT 'due',     -- due/running/done/failed/cancelled/expired
    started_at TEXT, finished_at TEXT, error TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_wheel_events_due ON wheel_events(status, due_at);

CREATE TABLE IF NOT EXISTS wheel_factor_state (
    persona_id TEXT NOT NULL,
    factor TEXT NOT NULL,
    subject_key TEXT NOT NULL,              -- position_id / symbol / account
    state_json TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (persona_id, factor, subject_key)
);

CREATE TABLE IF NOT EXISTS wheel_decisions (
    decision_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL,
    wheel TEXT NOT NULL,
    persona_id TEXT NOT NULL,
    subject_key TEXT NOT NULL,
    action TEXT NOT NULL,
    qty_pct REAL,
    mode TEXT NOT NULL,                     -- shadow / live
    executed INTEGER NOT NULL DEFAULT 0,
    reason TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (event_id, wheel, subject_key)
);

CREATE TABLE IF NOT EXISTS persona_proposals (
    proposal_id TEXT PRIMARY KEY,
    persona_id TEXT NOT NULL,
    kind TEXT NOT NULL,                     -- param_change / binding_change / strategy_toggle
    change_json TEXT NOT NULL,              -- 拟修改的 wheels.yaml 片段
    rationale TEXT NOT NULL,
    evidence_json TEXT,                     -- 依据的交易样本、归因
    backtest_report TEXT,
    status TEXT NOT NULL DEFAULT 'proposed',-- proposed/observe/backtested/approved/rejected
    reviewer TEXT, review_note TEXT,
    created_at TEXT NOT NULL, reviewed_at TEXT
);
```

记忆按角色隔离的迁移（兼容旧数据）：

```sql
ALTER TABLE watchlist ADD COLUMN persona_id TEXT;          -- 旧数据按 strategy_id/source 回填，无法判定的置 NULL（视为共享候选）
ALTER TABLE trade_attributions ADD COLUMN persona_id TEXT; -- 经 position_id 从 positions 回填
ALTER TABLE lessons ADD COLUMN persona_id TEXT;            -- 经 attribution_id 回填
```

- `wheel_decisions.mode=shadow` 用于影子期对比（见 10.1）。
- 止损二次确认的击穿记录迁入 `wheel_factor_state`，修复进程重启丢失（P7）。
- 新增表一律通过 `MemoryStore` 方法读写，新代码禁止直接 `_conn()`。

---

## 9. 模块布局

```
src/wheel/                       # 新包（不放在 src/ai_platform/）
  protocol.py                    # WheelEvent / Context / Opinion / Decision / FollowUp
  registry.py                    # DataStep / Factor 注册表（按 name 查找）
  runner.py                      # 执行 funnel / decision 转盘，产出 WheelResult
  dispatcher.py                  # 轮询 wheel_events，查绑定，调用 runner
  config.py                      # 读取 config/wheels.yaml，启动时做 schema 校验
  floor.py                       # SystemFloor
  exit_gateway.py                # ExitGateway
  persona.py                     # PersonaProfile 装配、as_of 记忆视图
  proposals.py                   # persona_proposals 读写与审批流转
  data/                          # DataStep 实现（live / historical 两套）
  factors/
    floor_*.py  rule_*.py  mod_*.py  screen_*.py  detector_*.py  llm_*.py
config/wheels.yaml               # 转盘定义 + 角色绑定
```

- `config/trading_persona.yaml` 保留人格描述（哲学、风格、资金），绑定迁入 `wheels.yaml`；迁移完成前两者并存，`daily_pipeline` / `intraday_llm_review` 开关由绑定替代后删除。
- 启动时配置校验失败（引用了未注册的 Factor、参数类型错误、事件无效）→ scheduler 拒绝启动并告警，而不是运行时报错。

---

## 10. 可观测性、测试与回测

### 10.1 影子模式

每个阶段先以 `mode=shadow` 运行：转盘照常运行并写 `wheel_decisions`，但不调用 ExecutionEngine；同时旧路径照常执行。每日 16:00 health_check 输出对比报告：

| 字段 | 含义 |
|---|---|
| 一致 | 转盘与旧路径动作相同 |
| 转盘更严 | 旧路径卖/买，转盘 HOLD（需逐条确认是否为预期，如 301697 类） |
| 转盘更松 | 旧路径 HOLD，转盘卖/买（必须为零或逐条解释后才能切换） |

### 10.2 测试层次

1. **Factor 单测**：每个 Factor 输入 Context fixture，断言 Opinion。
2. **转盘黄金用例**：用生产真实场景固化回归用例，至少包含：600383（同日买卖）、002487（单 tick 击穿后收复）、301697（LLM 绕过止损确认）、002285（09:30 旧数据）、600809（价值股 -8% 噪音）、休市日、无真实行情强平。
3. **配置校验测试**：`config/wheels.yaml` 全量加载、每个绑定可解析。
4. **Dispatcher 测试**：后续事件的次数上限、截止时间、午休顺延、持仓平仓后作废、幂等。

### 10.3 回测同源

- `strategy_lab` 增加 `WheelReplay`：按交易日生成 `clock.*` 事件，DataStep 使用历史实现，对同一 `config/wheels.yaml` 运行。
- 日线回测只能回放日级事件；分钟级转盘（如 `morning_watch`）需要分钟 K 线数据，在具备之前只能以模拟盘验证，报告中必须注明。
- 研究闸门增加"随机买点对照"（见 2026-10 对照实验）：策略每笔平均收益需显著优于同股票池随机买点。

---

## 11. 现有代码映射

| 现有代码 | 新架构中的位置 |
|---|---|
| `scheduler.py` 的 20+ 个 job | 事件源：只写 `wheel_events`；健康检查等运维 job 保留 |
| `trading_calendar`、`data_quality` 闸门 | `floor.*` |
| `evaluate_hard_exit` | `rule.stop_loss` / `rule.max_loss` / `rule.target_reduce` / `rule.narrow_stop` / `rule.pulse_take_profit` |
| `stop_confirm` | `mod.stop_confirm`（状态入 `wheel_factor_state`） |
| `_sold_today` | ExitGateway 的 REDUCE 限频 |
| `position_reviewer`（LLM） | `llm.position_review`（propose，priority ≥ 500）＋ `mod.value_noise_filter` |
| `position_reviewer._apply_rule_override` | 拆分为对应 `rule.*` / `mod.*`，删除重复实现 |
| `execute_force_close_ultra_short` | `overnight_force_close` 转盘 |
| `execute_morning_pulse` | `morning_watch` 转盘 + `followup.morning_watch` |
| `execute_tail_hunt` | `market_screen_ultra`（funnel）+ `entry_confirm_ultra`（decision） |
| `check_cold_period`、上涨家数、panic 闸门 | `veto.cold_period` / `veto.weather_up_ratio` / `veto.regime_panic` |
| LangGraph：market_brain / explorer | `data.regime_snapshot` + `market_screen` funnel |
| LangGraph：strategist（LLM）/ detectors | `llm.signal_rank` / `detector.*`（propose） |
| LangGraph：risk_governor | `mod.position_cap` / `mod.sector_concentration` |
| LangGraph：executioner（immediate 当场成交） | 改为写信号 + `clock.next_open` 绑定的 `order_fill_at_open` 转盘（修复 P6） |
| `_check_pending_signals` | `pending_trigger` 转盘（绑定 `clock.intraday_5m`） |
| `regime_drift._trigger_emergency_review` | 写 `market.regime_drift` 事件，绑定 `llm_position_review`（修复 P5） |
| `stale_position_check` | `rule.held_too_long`（propose） |
| `ShortTermWatchlistManager.run_daily_maintenance` | `watchlist_maintain_ultra` 转盘 |
| `trading_persona.yaml` 的哲学/风格/资金 | 角色档案 · 身份层 |
| `daily_pipeline` / `intraday_llm_review` 开关、persona_id 子串判断价值股 | 角色档案 · 行为配置（bindings、`kind`） |
| `experience_layer.StockMemory` / `StrategyLedger` / `DynamicBlacklist` | 记忆视图 `persona.stock_memory` / `strategy_stats` / `blacklist`（改为按角色隔离） |
| `agents/memory/trade_attribution.py`、`closing_analysis` | `attribution` / `daily_journal` 记忆类转盘 |
| `meta_rule_synthesizer`、`persona_updater`、`weekly_feedback` | `weekly_reflection` 转盘，只产出 `persona_proposals` |

---

## 12. 实施计划

总原则：**每个阶段都可独立部署、先影子后切换、可一键回滚**。回滚方式统一为把对应绑定的 `mode` 从 `live` 改回 `shadow`，旧路径在该阶段验收完成前不删除。

### Phase 0 — 骨架与协议（不改变任何线上行为）✅ 已实现

`src/wheel/`（protocol / registry / runner / config / floor / exit_gateway / persona / shadow + data/ + factors/）、`config/wheels.yaml`、迁移 `004_wheel_tables`、合并规则单测已落地。

| 任务 | 产出 |
|---|---|
| 0.1 | `src/wheel/protocol.py`、`registry.py`、`runner.py`（funnel/decision 两种合并规则） |
| 0.2 | `config.py` + `config/wheels.yaml` schema 校验，启动校验失败拒绝启动 |
| 0.3 | MemoryStore 迁移：`wheel_events`、`wheel_factor_state`、`wheel_decisions` 三张表及读写方法 |
| 0.4 | `floor.py`（复用 trading_calendar / data_quality），DataStep 缓存机制 |
| 0.5 | 单测：合并规则（veto 截断、priority、modify 只能调弱、LLM 低优先级） |

验收：全量测试通过；部署后线上行为零差异；新表在 NAS 库中创建成功。

### Phase 1 — 出场转盘 + ExitGateway（优先，直接修复 P1）✅ 影子模式已实现

`exit_confirm` / `exit_confirm_ultra` / `overnight_force_close` / `llm_position_review` 四个转盘已接进 hard_scan / intraday_review / force_close 三条旧路径并行运行（mode=shadow，只写 `wheel_decisions` 不执行）；止损确认状态落 `wheel_factor_state`；16:00 health_check 输出 `wheel_shadow_report`。

复审后修复（影子语义与 live 对齐）：
- 止损确认最小观测间隔 ≥180s，同一时刻多转盘评估不再重复计数；
- "-8% 熔断"收复判定看止损条件本身而非价格是否高于止损价（修窗口绕过）；
- 复审中的规则覆盖动作经 `rule.review_override` 以规则优先级表达，
  `llm.*` 只消费 `llm_raw_*` 原始判断，不再被降噪/最低持仓误拦；
- veto 阶段缺数据/异常一律否决（fail-closed）；
- `wheel_decisions` 记录 `decided_by`/`gateway` 结果；live 时旧路径自动让位；
- 对比报告新增 gateway_rejected / uncovered 两桶，未覆盖单独告警。

待 1.8 连续 5 个交易日"转盘更松=0 且 uncovered 可解释"后切 live（mode 切 live 即自动让旧路径让位，改回 shadow 即回滚）。

| 任务 | 产出 |
|---|---|
| 1.1 | `exit_gateway.py`：持仓锁、同窗口去重、REDUCE 限频、真实价校验、平仓后取消后续事件 |
| 1.2 | `rule.*` Factor：从 `evaluate_hard_exit` 拆出，`hard_rules.py` 改为调用这些 Factor（回测引擎同步受益） |
| 1.3 | `mod.stop_confirm`（状态入库）、`mod.reduce_daily_cap`、`mod.value_noise_filter` |
| 1.4 | `llm.position_review`：封装现有 LLM 复审，只输出提议，priority ≥ 500 |
| 1.5 | `exit_confirm`、`overnight_force_close` 转盘；四个角色的出场绑定 |
| 1.6 | 影子模式：hard_scan / intraday_review / force_close 照旧执行，转盘并行写 shadow 决策，16:00 输出对比 |
| 1.7 | 黄金用例：600383、002487、301697、002285、600809、无真实行情强平 |
| 1.8 | 切换：对比报告连续 5 个交易日"转盘更松=0"后，出场改为转盘 live；旧出场路径改为只读并保留 2 周后删除 |

验收：LLM 不再能绕过止损确认；同一持仓不会被两条路径重复卖出；止损确认状态重启不丢失；隔夜客硬编码在出场链路中清零。

### Phase 2 — 事件层与后续事件（实现角色自定义观察，修复 P3/P5）

实施拆分（Phase 1 观察期不变更出场触发方式与旧路径时序）：

| 任务 | 产出 | 状态 |
|---|---|---|
| 2.1 | `dispatcher.py`：15 秒轮询、超时、重启恢复、交易时段/午休/截止时间/次数上限/作废规则；scheduler 内 daemon 线程，只接管 `followup.*`/`market.*`/`account.*`/`position.*` | ✅ 已实现 |
| 2.3 | `regime_drift` 写 `market.regime_drift` 事件 → 广播 `urgent_review` 转盘逐持仓记 REVIEW 影子决策，修复紧急复审断链（live 后由 Dispatcher 映射到复审入口） | ✅ 影子 |
| 2.4 | `morning_watch` 转盘 + `mod.watch_followup` 登记 `followup.morning_watch`（默认 1 分钟、30 次、14:30 截止），脉冲检查后链式复查 | ✅ 影子 |
| 2.6 | 只读 API `/api/wheel/events` + `/api/wheel/decisions`（轨迹另经 AgentWorkspace 事件流可见） | ✅ 已实现 |
| 2.2 | scheduler 时钟 job 改为写 `clock.*` 事件 | ⏸ 待 Phase 1 切 live（会改变出场触发方式，污染对比基线） |
| 2.5 | 首次 LLM 复审事件从 09:30 改为 10:00 | ⏸ 待 Phase 1 切 live（改变旧路径时序） |
| — | `daily_shadow_report` 只统计 Phase 1 四个出场转盘（GATE_WHEELS），其余转盘单列 `other_wheels` | ✅ 已实现 |

回审修复：
- 影子模式同窗口去重按转盘组隔离（`protocol.wheel_cohort`：GATE_WHEELS 一组，其余各自一组）——新转盘的影子"会执行"不得把门槛转盘的"更松"掩盖成"网关拒绝"；live 去重只看 live 决策、不分转盘。
- 行情暂时不可用（`floor.real_quote_required` 否决）时，观察类修饰（`observer=True`，只登记后续事件）照常运行，链条不因一次网络抖动断掉；次数上限/截止时间不变。
- `output` 启动校验（`exit_proposal` / `urgent_review` / `candidates`）；广播逐持仓隔离异常。

验收：后续事件不会越过截止时间、不会无限复查、持仓平仓后在途事件自动作废；`morning_watch` 影子 10 个交易日，人工复核其决策后再切 live。

### Phase 3 — 入场转盘（修复 P6，统一入场语义）

| 任务 | 产出 |
|---|---|
| 3.1 | 隔夜客：`market_screen_ultra` + `entry_confirm_ultra`，替代 `execute_tail_hunt`（影子→切换） |
| 3.2 | `order_fill_at_open` 转盘：daily_pipeline 的 immediate 信号改为次日 09:30 开盘价＋滑点成交，与回测一致 |
| 3.3 | `pending_trigger` 转盘：从 hard_scan 中拆出条件单触发 |
| 3.4 | 短线/价值：`market_screen`（funnel，复用 scanner 步骤）+ `entry_confirm`（detector + LLM 排序 + risk_governor 修饰）；LangGraph 节点函数作为 Factor/DataStep 复用，管线结构迁移完成后 `workflow.py` 仅保留兼容入口 |
| 3.5 | 执行器白名单、`daily_pipeline` 开关改为读 PersonaPolicy，删除硬编码 |
| 3.6 | 角色档案加载：`PersonaProfile` 装配、as_of 截取、惰性加载记忆视图；`kind` 取代价值股子串判断 |
| 3.7 | 记忆按角色隔离：`watchlist` / `trade_attributions` / `lessons` 增加 persona_id 并回填；`StockMemory`、`StrategyLedger` 按角色查询；新增 `shared.stock_flags` |
| 3.8 | `attribution`、`watchlist_maintain` 记忆类转盘替代现有归因回补与观察池维护 job |

验收：新开仓全部为盘中或开盘可成交的价格；入场决策轨迹可在监控页逐条解释；隔夜客硬编码清零；一个角色的止损/冷却不再影响其他角色。

### Phase 4 — 账户风控事件

| 任务 | 产出 |
|---|---|
| 4.1 | `account_monitor` DataStep：当日组合盈亏、连续亏损笔数 |
| 4.2 | `account.daily_loss_breach` → 当日禁止新开仓。现状：`ExecutionEngine._check_circuit_breaker` 使用全局常量（日亏 3%），未读取人格配置 `max_daily_loss_pct` |
| 4.3 | `account.consecutive_losses` → 降频/降仓位修饰。现状：执行器熔断为"14 天内连亏 5 笔"全局常量，人格配置 `consecutive_loss_limit: 3` 未生效 |
| 4.4 | 执行器熔断改为读 PersonaPolicy，账户事件与执行器熔断共用同一计算函数 |

验收：人格配置中的账户级风险限制全部按人格生效，有代码执行路径与测试。

### Phase 4b — 复盘提议通道

| 任务 | 产出 |
|---|---|
| 4b.1 | `persona_proposals` 表与 MemoryStore 方法 |
| 4b.2 | `weekly_reflection` 转盘：归纳教训写记忆；规则类结论只写提议；样本不足标记 `observe` |
| 4b.3 | `meta_rule_synthesizer` / `persona_updater` 改为提议生产者，移除直接回写配置的路径 |
| 4b.4 | 参数变更类提议自动触发 strategy_lab 回测（含随机买点对照），报告挂到提议上 |
| 4b.5 | 前端审批页：查看依据与回测报告，批准后生成 `wheels.yaml` 变更并提交 git |

验收：系统中不存在任何未经审批即修改行为配置或身份配置的代码路径。

### Phase 5 — 回测同源

| 任务 | 产出 |
|---|---|
| 5.1 | 历史版 DataStep（日线缓存、历史热点板块、历史 PE），`WheelReplay` |
| 5.2 | `strategy_lab --wheel <persona>`：按角色绑定回放日级事件 |
| 5.3 | 研究闸门增加随机买点对照（策略每笔均值需优于随机分布） |

验收：同一角色配置在回测与线上产生的日级决策逻辑一致；报告注明不可回放的分钟级转盘。

### Phase 6 — 清理

| 任务 | 产出 |
|---|---|
| 6.1 | 删除旧出场/入场路径、`_execute_review_action`、`daily_pipeline`/`intraday_llm_review` 开关 |
| 6.2 | 全仓 `ultra_short_momentum_v1` 硬编码清零（测试与配置除外） |
| 6.3 | 新增与改动代码中的 `brain.store._conn()` 迁入 MemoryStore 方法 |
| 6.4 | 更新 `AGENTS.md`、`runtime_overview.md`、`event_catalog.md` |

---

## 13. 风险与对策

| 风险 | 对策 |
|---|---|
| 配置演变成编程语言，难以调试 | 配置只允许选择/顺序/参数；启动 schema 校验；逻辑一律写成有测试的 Factor |
| 迁移期新旧路径重复下单 | 影子期转盘不调用执行器；切换以绑定为粒度，旧路径同步改只读 |
| Dispatcher 故障导致全天停摆 | 复用现有 watchdog 心跳；事件积压超过阈值 `notify_ops`；scheduler 时钟 job 在 Phase 2 验收前保留原路径 |
| 分钟级后续事件依赖实时行情稳定性 | `floor.real_quote_required`：无真实行情时后续事件顺延而不是决策；连续失败告警 |
| 抽象过度、工期拉长 | 每个 Phase 只迁移一类动作，Phase 1 单独即可交付价值；Phase 5/6 可视情况推迟 |
| LLM Factor 输出不稳定 | LLM 只输出结构化 Opinion，schema 校验失败视为 `skip`；优先级固定低于规则 |
| 角色从少量交易中"学坏" | 经验只写记忆；规则修改必须走提议→回测闸门→人工审批；样本不足只能 `observe` |
| 记忆串角色 | 记忆默认按角色隔离；只有客观事实进入 `shared.stock_flags` |
| 回测使用未来记忆 | 记忆视图强制 as_of 截取，回放测试覆盖 |

---

## 14. 与 AGENTS.md 约束的对照

| 约束 | 本设计 |
|---|---|
| 禁止向 `src/ai_platform/` 新增功能 | 新包为 `src/wheel/`，不复用、不扩展 ai_platform EventBus / workflow_engine |
| 禁止新增 SQLite 文件 | 三张新表均在 `central_brain.db`，经 MemoryStore 迁移创建 |
| 禁止往 Prompt 里加新策略名 | 新策略只以 `detector.*` Factor 形式加入 |
| 硬规则必须是代码 | SystemFloor、ExitGateway、`rule.*`、`mod.*` 全为确定性代码；LLM 只能提议 |
| `SIGNAL_FACTORY=1` 需过闸 | 入场转盘中 `detector.*` 的启用仍受研究闸门约束 |
| 自动回写 persona/prompt 默认关闭 | 转盘不写回任何配置文件；复盘只产生 `persona_proposals`，经回测与人工审批后才修改 `wheels.yaml`，身份层只能人工修改 |
