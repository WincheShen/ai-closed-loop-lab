# Phase 3.5 — 可观测性与中央数据层

> 状态：**部分实现 / 持续迭代**
> 起草日期：2026-04-28
> 前置 Phase：Phase 3（真实 TradingAgents 接入，已完成）
> 拦截项：在继续做 Phase 3 剩余（Webhook→SMA / 复盘归因）之前必须先做

## 0. 实现进度快照

| 切片 | 设计章节 | 代码落地 | 测试 | 备注 |
|------|---------|---------|------|------|
| S1 LLM 打点 | §4.1 `llm_calls` / §5.1 callback | ✅ `MemoryStore.record_llm_call` + `src/trading_agent_service/analysis/observability.py::LLMUsageCallback` | ✅ `tests/test_llm_observability.py`（5/5） | callback 尚未挂到 `RealTradingAgentsAdapter` 的 `ChatOpenAI.callbacks`，需要时再接 |
| S2 daily_picks 归档 | §4.1 `daily_picks` / §5.2 | ✅ `MemoryStore.save_daily_pick` + `DailyScanPipeline._archive_to_central_brain` | ✅ `tests/test_observability_store.py`（6/6） | 注意表名避开与旧 `data/daily_picks/*.json` 冲突，新表叫 `daily_picks_archive` |
| S2 social_posts 归档 | §4.1 `social_posts` / §5.3 | ✅ `MemoryStore.record_social_post` + `DailyScanPipeline._record_social_post` | ✅（同上测试文件） | 只在 `DailyScanPipeline._dispatch_to_sma` 成功分支落一条；webhook → SMA 的触发点仍在 Phase 3 待办里 |
| S3 SMA 同步器 | §5.4 `sync_sma_engagements.py` | ⏳ `MemoryStore.update_social_post_metrics` 已有，脚本本身未落 | ⏳ | 依赖 SMA 项目 `web_tasks.db` 只读 attach，留给后续 |
| S4 Streamlit Dashboard | §6 | ✅ `scripts/dashboard.py`（三 Tab） | ⏳（UI 类无单元测试） | `streamlit run scripts/dashboard.py --server.port 8501` |
| 定价表 | §7 `config/llm_pricing.yaml` | ⚠️ 代码里自带内置 fallback 价格；YAML 文件本身未落盘 | ✅（估算逻辑有测试） | 合同价敲定后把 YAML 补上即可覆盖 |

**约定**：
- ✅ = 已完成，pytest 覆盖
- ⚠️ = 部分完成 / 有 fallback
- ⏳ = 设计稳定，尚未落代码
- 以上不包括真实环境跑 tradingAgents 的端到端打点验证（需 Azure OpenAI key）

---

## 1. 问题陈述

Phase 3 跑通后暴露的三个系统性问题：

| # | 问题 | 实际影响 |
|---|------|---------|
| ① | 数据落地**散文件**，无统一入口 | 想回答"昨天推了哪只票、agent 打了什么分、帖子发在哪、互动多少"需要翻 3 个目录 + 2 个库 + 2 个项目 |
| ② | **没有社媒运营 dashboard** | 不知道每个账号每天发了多少、哪篇最火、互动趋势 |
| ③ | **LLM 调用完全是黑盒** | Phase 3 跑 3 只股票花了多少钱？不知道。慢的原因是哪个 agent？不知道。 |

**影响升级：** 如果继续推 Webhook→SMA 和复盘归因，数据散落会更严重——沈经理每笔交易触发一次分析，一周就是上百次调用、上百份报告、几十个帖子，没有统一存储和 dashboard 根本无法运营。

---

## 2. 现状盘点

### 2.1 ai-closed-loop-lab（本项目）

| 位置 | 类型 | 内容 | 启用状态 |
|------|------|------|---------|
| `src/central_brain/metadata_store.py` | 代码 | SQLite + EventBus 框架，7 张表（sessions/events/memories/trade_signals/orders/fills） | ⚠️ **写好但从未被任何模块调用** |
| `data/trading_agent_service/cache.sqlite` | SQLite | `reports` 表（symbol, evaluated_date, price_low/high, decision, report_path） | ✅ 在用，但只是缓存 |
| `data/trading_agent_service/reports/*.json` | 散文件 | 每次 analyze 的完整 Report JSON | ✅ 在用 |
| `data/daily_picks/*.json` | 散文件 | 每日选股结果 | ✅ 在用 |
| `data/central_brain.db` | SQLite | 设计中的中央库 | ❌ 文件不存在 |

### 2.2 social-media-automation（姐妹项目）

| 位置 | 内容 |
|------|------|
| `data/state/web_tasks.db` → `tasks` 表 | 创作任务：id / account_id / title / content / post_url / error |
| `data/state/monitor_tasks.db` → `monitor_tasks` 表 | 监控任务：post_url / metrics_json（含点赞评论数）/ status |
| `web/` | 创作任务前端（不是运营 dashboard） |

### 2.3 关键空白

- ❌ **LLM 调用级打点**：ChatOpenAI/LangChain callback 没注入
- ❌ **跨库 join 键**：ai-lab 这边不知道选股推到 SMA 后的 task_id，SMA 侧也不知道帖子源自哪次选股
- ❌ **运营 dashboard**：完全没有

---

## 3. 架构决策（ADR 形式）

### ADR-001：复活 central_brain 而不是另起

**决策**：扩展现有 `src/central_brain/metadata_store.py`，加新表；不新建独立 observability 模块。

**理由**：
- 现有 7 张表（trade_signals/orders/fills）本就是为 Phase 2 设计的，不应废弃
- EventBus 设计合理，可直接用于写入触发
- 避免两套"中央"概念

**代价**：
- `MemoryStore` 会变大（建议分拆为 `core_store.py` + `observability_store.py`，门面仍是 `CentralBrain`）

### ADR-002：SQLite 作为中央存储，不上 Postgres

**决策**：central_brain.db 用 SQLite。

**理由**：
- 单机个人项目，SQLite 写入性能足够（日峰值 <1000 次写入）
- 零运维
- Streamlit dashboard 直接 `sqlite3.connect` 读最方便

**代价**：
- 多进程写入需要 WAL 模式 + 显式 lock（已有 `threading.Lock`，够用）
- 将来要上线多用户可以迁 Postgres（schema 基本兼容）

### ADR-003：Dashboard 用 Streamlit，不自己写前端

**决策**：`scripts/dashboard.py` = Streamlit 单文件。

**理由**：
- 0 前端代码，数据看板场景 Streamlit 原生支持图表、表格、筛选
- 一条命令 `streamlit run scripts/dashboard.py` 起服务
- 修改即生效，运营场景足够

**备选方案与否决理由**：
- FastAPI + Jinja2：UI 代码量太大，没必要
- Grafana：要接 Prometheus 或 SQLite 插件，部署麻烦
- 嵌入 SMA 的 `web/`：两边耦合，且 SMA web 是创作向不是运营向

### ADR-004：LLM 打点走 LangChain BaseCallbackHandler

**决策**：在 `RealTradingAgentsAdapter._get_graph()` 里给 TradingAgentsGraph 的 LLM 客户端注入 callback。

**理由**：
- LangChain 原生支持，不侵入 tradingAgents_neo 代码
- `on_llm_start` / `on_llm_end` 能拿到 model、token usage、latency
- 用 `context var` 传 `request_id` 和 `symbol`，让 callback 能关联是哪次 /analyze 的哪个 agent

**代价**：
- tradingagents 的内部 agent 调用链有些会跳过标准 callback（比如直接 HTTP），需要验证覆盖率

### ADR-005：跨库关联用"双写 + task_id"

**决策**：
- `social_media_dispatcher.SmaClient.dispatch()` 成功后，在 ai-lab 的 `social_posts` 表里**立即写一条**，保存 SMA 返回的 `task_id`
- Dashboard 读 `social_posts` 时，用 `task_id` 反查 SMA 的 `web_tasks.db` + `monitor_tasks.db`（只读，跨文件路径 attach）

**理由**：
- 避免写分布式事务
- SMA 库是 source of truth 的互动数据，ai-lab 这边是 source of truth 的"这是哪次选股推出去的"

### ADR-006：现有散文件**不回填**

**决策**：Phase 3.5 上线后只记录**新**数据。旧的 `data/daily_picks/2026-04-27.json` 不回填到 SQLite。

**理由**：历史数据量极小（1 天），没必要写迁移脚本。

---

## 4. 数据模型

### 4.1 新增 3 张表（挂在 central_brain.db）

```sql
-- ─────────────────────────────────────────────────────────────
-- LLM 每次调用打点（解决问题 ③）
-- ─────────────────────────────────────────────────────────────
CREATE TABLE llm_calls (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              TEXT    NOT NULL,          -- ISO datetime
    request_id      TEXT    NOT NULL,          -- 关联到一次 /analyze
    symbol          TEXT,                      -- 600519 etc.
    stage           TEXT,                      -- market|news|social|funds|bull|bear|trader|risk|judge|signal
    model           TEXT    NOT NULL,          -- gpt-5.3-chat
    provider        TEXT,                      -- azure|openai|anthropic
    prompt_tokens   INTEGER DEFAULT 0,
    completion_tokens INTEGER DEFAULT 0,
    total_tokens    INTEGER DEFAULT 0,
    cost_usd        REAL    DEFAULT 0.0,       -- 按模型价目表估算
    latency_ms      INTEGER,
    success         INTEGER DEFAULT 1,
    error_msg       TEXT,
    meta_json       TEXT                       -- 扩展字段
);
CREATE INDEX idx_llm_ts      ON llm_calls(ts);
CREATE INDEX idx_llm_symbol  ON llm_calls(symbol);
CREATE INDEX idx_llm_request ON llm_calls(request_id);

-- ─────────────────────────────────────────────────────────────
-- 每日选股归档（解决问题 ①）
-- ─────────────────────────────────────────────────────────────
CREATE TABLE daily_picks (
    pick_date            TEXT PRIMARY KEY,     -- YYYY-MM-DD
    is_mock_data         INTEGER DEFAULT 0,
    hot_sectors_json     TEXT,                 -- JSON array
    candidates_count     INTEGER DEFAULT 0,
    agent_calls_count    INTEGER DEFAULT 0,
    aggressive_json      TEXT,                 -- JSON array of symbols
    stable_json          TEXT,
    total_llm_cost_usd   REAL    DEFAULT 0.0,  -- 该日 llm_calls 汇总（冗余，方便查询）
    elapsed_seconds      REAL    DEFAULT 0.0,
    picks_file_path      TEXT,                 -- 仍保留散文件，这里记路径
    created_at           TEXT    NOT NULL
);

-- ─────────────────────────────────────────────────────────────
-- 社媒发帖追踪（解决问题 ②）
-- ─────────────────────────────────────────────────────────────
CREATE TABLE social_posts (
    sma_task_id          TEXT PRIMARY KEY,     -- = SMA tasks.id
    account_id           TEXT    NOT NULL,     -- XHS_01 / DY_02 ...
    platform             TEXT    NOT NULL,     -- xhs | dy | wb
    source_pick_date     TEXT,                 -- 该帖源自哪天的 daily_picks
    source_symbols_json  TEXT,                 -- JSON array
    topic                TEXT,                 -- 主题/标题线索
    dispatched_at        TEXT    NOT NULL,     -- ai-lab dispatch 的时刻
    sma_status           TEXT    DEFAULT 'pending', -- 从 SMA tasks.status 同步
    post_url             TEXT,                 -- 从 SMA tasks.post_url 同步
    published_at         TEXT,
    last_metrics_json    TEXT,                 -- 从 SMA monitor_tasks.metrics_json 同步
    last_metrics_at      TEXT,
    error                TEXT
);
CREATE INDEX idx_posts_account ON social_posts(account_id);
CREATE INDEX idx_posts_date    ON social_posts(dispatched_at);
```

### 4.2 关系图

```
daily_picks (date=PK)
    │ source_pick_date
    ▼
social_posts (sma_task_id=PK)
    │ sma_task_id
    ▼ (attach SMA 库只读查)
sma/web_tasks.tasks (id=PK)
    │ post_url
    ▼
sma/monitor_tasks (post_url)

llm_calls (id=PK)
    │ request_id
    └── 关联一次 /analyze；symbol 可回查 reports 详情
```

### 4.3 已有表不改

- `reports`（trading_agent_service/cache.sqlite）：**保持独立**。它是 Report Payload 缓存，生命周期和 observability 不同。Dashboard 需要看详情时按 `symbol` + `date` 从 central_brain.daily_picks 跳过去。
- `trade_signals / orders / fills`（central_brain）：保留给 Phase 2 交易联动。Phase 3.5 不动。

---

## 5. 写入接入点

### 5.1 LLM callback（3.5 的核心）

**文件**：`src/trading_agent_service/analysis/observability.py`（新建）

```
LLMUsageCallback(BaseCallbackHandler):
    on_llm_start  → 记录 request_id/stage/ts
    on_llm_end    → 读 response.llm_output.token_usage → 写 llm_calls
    on_llm_error  → 写失败记录

context 变量：
    _current_request_id: ContextVar[str]
    _current_symbol:     ContextVar[str]
    _current_stage:      ContextVar[str]   # adapter 层在 propagate 前 set
```

**注入位置**：
- `RealTradingAgentsAdapter._get_graph()`：给 `ChatOpenAI` 的 `callbacks=[LLMUsageCallback()]`
- `RealTradingAgentsAdapter.analyze()`：生成 `request_id = uuid4()`，`set(_current_request_id, request_id)`

**价格表**（`cost_usd` 估算）：
- 放 `config/llm_pricing.yaml`，{model: {prompt_per_1k: 0.XX, completion_per_1k: 0.YY}}
- Azure deployment 用户自定义 → 允许 override

### 5.2 daily_picks 归档

**文件**：`src/stock_analyzer/pipelines/daily_scan.py:run()` 末尾新加：
```
central = get_central_brain()
central.store.save_daily_pick(picks, llm_cost=<sum llm_calls today>)
```

### 5.3 social_posts 登记

**文件**：`src/social_media_dispatcher/sma_client.py:SmaClient.dispatch()` 成功分支：
```
if result.success:
    central.store.record_social_post(
        sma_task_id=result.sma_task_id,
        account_id=payload.account_id,
        ...
    )
```

### 5.4 social_posts 定时同步（读 SMA 库）

**文件**：`scripts/sync_sma_engagements.py`（新建，可 cron）
```
对 social_posts.sma_status != 'completed' 的记录，
attach SMA web_tasks.db 查 status/post_url，
attach SMA monitor_tasks.db 查 metrics_json → 更新
```

建议节奏：每 30 分钟跑一次，或 dashboard 打开时按需刷新。

---

## 6. Dashboard 规格

**文件**：`scripts/dashboard.py`（Streamlit 单文件，~200-300 行）

**启动**：
```bash
conda activate ai-lab
streamlit run scripts/dashboard.py --server.port 8501
```

### Tab 1 — 今日选股

- **顶部指标卡**：今日选股日期 / 候选池数 / Agent 调用数 / 本日 LLM 成本 / 激进+稳健数
- **热点板块**：标签云或横向柱
- **激进推荐表格**：symbol, name, change_pct, rule_score, agent_decision, agent_confidence, summary（可点展开）
- **稳健推荐表格**：同上
- **候选池**：可折叠，带筛选（按决策/行业/涨幅）
- 日期选择器回看历史（从 daily_picks 查）

### Tab 2 — LLM 成本

- **顶部指标卡**：今日成本 / 近 7 天成本 / 近 30 天 / 累计调用数 / 平均每次 $
- **按天柱图**：近 30 天每日成本
- **按 stage 饼图**：哪个环节最烧钱（market analyst vs 辩论）
- **按 model 表格**：model, 调用数, prompt_tokens 总, completion_tokens 总, 成本
- **贵的 symbol top 10**：哪些票 LLM 成本最高（可能是 propagate 失败重试的问题）
- **慢的调用 top 10**：latency 倒序

### Tab 3 — 社媒运营

- **顶部指标卡**：今日发帖数 / 本周发帖数 / 本月发帖数 / 累计互动
- **账号分组表格**：account_id, platform, 今日发 / 本周发 / 最近一篇时间
- **最新帖子**：post_url（可点开），title, 发布时间, 点赞/评论/转发, 源自哪次选股
- **互动趋势**：某个 post_url 的 metrics 时序（从 monitor_tasks 历史查）

---

## 7. 成本估算方法

### 7.1 Azure OpenAI gpt-5.3-chat 价目（示意，你按合同填）

```yaml
# config/llm_pricing.yaml
models:
  gpt-5.3-chat:
    provider: azure
    prompt_per_1k_usd: 0.005       # 填你实际合同价
    completion_per_1k_usd: 0.015
  gpt-4o-mini:
    provider: openai
    prompt_per_1k_usd: 0.00015
    completion_per_1k_usd: 0.00060
  deepseek-chat:
    provider: deepseek
    prompt_per_1k_usd: 0.00014
    completion_per_1k_usd: 0.00028
```

### 7.2 公式

```
cost_usd = prompt_tokens * prompt_per_1k / 1000
         + completion_tokens * completion_per_1k / 1000
```

### 7.3 tradingagents 内部成本覆盖率验证

Phase 3.5 上线第一天要跑一次对比：
- `~/.tradingagents/logs/` 里的调用数
- `llm_calls` 表里的调用数
两者应基本一致。如果 callback 漏了某个 agent（比如 tool-calling 内部调用），补注入。

---

## 8. 交付节奏（最小可用切片）

| 切片 | 时间 | 产物 | 验收 |
|------|------|------|------|
| **S1 LLM 打点** | 0.5 天 | `llm_calls` 表 + `LLMUsageCallback` + 注入 | 跑一次 `/analyze`，llm_calls 至少有 12 条记录，cost_usd 非 0 |
| **S2 daily_picks + social_posts 归档** | 0.5 天 | 两张表 + 两处写入 | 跑一次 daily_scan，daily_picks 一行；dispatch 一次，social_posts 一行 |
| **S3 SMA 同步器** | 0.5 天 | `sync_sma_engagements.py` + attach SMA db | 帖子 24h 后 last_metrics_json 有数据 |
| **S4 Streamlit Dashboard** | 0.5 天 | 3 Tab | 三个 tab 都能加载真实数据 |

**总计：2 天。** 每个切片独立可用，可以分两次工作完成。

---

## 9. 开放问题（待你决定）

| # | 问题 | 我的默认倾向 |
|---|------|-------------|
| Q1 | Azure gpt-5.3-chat 实际 token 价格是多少？ | 等你填 `config/llm_pricing.yaml` |
| Q2 | MemoryStore 大表要不要拆文件？ | 建议：新建 `observability_store.py`，`CentralBrain` 加 `.obs` 属性 |
| Q3 | Dashboard 要不要加登录？ | 默认 localhost only，不加；真要对外再套 Nginx basic auth |
| Q4 | SMA 的 monitor_tasks 数据频率如何？ai-lab 要 push 还是 pull？ | pull（sync 脚本按需拉），更松耦合 |
| Q5 | 价格无法覆盖的 tool call（比如 akshare 搜索）是否也要打点？ | Phase 3.5 不做，Phase 4 再说 |
| Q6 | dashboard 里要不要加"单股深度报告"详情页（跳 markdown）？ | 建议加，点 symbol 跳转 `/reports/{symbol}_{date}.json` 渲染 |

---

## 10. 不做的事

明确 Phase 3.5 **不包括**：

- 告警 / 阈值监控（Phase 4 再说）
- 多租户 / 权限（还是单用户单机）
- 交易订单埋点到 observability（trade_signals 已有独立表，Phase 2 责任）
- Grafana / Prometheus / 远程日志聚合（SQLite 单机够用）
- 数据回填历史散文件（`daily_picks/2026-04-27.json` 不导入）

---

## 11. 后续依赖

Phase 3.5 完成后解锁：
- **Phase 3 原计划**：Webhook→SMA 自动触发（这时 dashboard 能直接看到每笔交易触发的帖子）
- **Phase 4 复盘归因**：T+1/T+5 收益回填有 `social_posts.source_symbols_json` 可 join
- **Phase 5 并发优化**：`llm_calls.latency_ms` 能精确定位瓶颈在哪个 stage

Phase 3.5 是**数据基础设施**，不是功能，但它解锁后续所有运营决策。
