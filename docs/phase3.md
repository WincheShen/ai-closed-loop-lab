# Phase 3 — 接入真实 TradingAgents

> 完成日期：2026-04-27
> 对应需求：[requirements.md](./requirements.md) FR-1.1 真实 TradingAgent 输出
> 替换：Phase 1 的 `MockAnalyzer`

## 交付清单

- ✅ `src/trading_agent_service/analysis/tradingagents_adapter.py`
  - `RealTradingAgentsAdapter` —— 调 `TradingAgentsGraph.propagate()`
  - `_normalize_decision` —— 容错的 BUY/HOLD/SELL 提取（容忍带句号、解释）
  - `_load_stock_meta` —— 从 akshare 拉现价 / pe / pb / 市值 / 行业（数值字段不走 LLM）
  - `_get_graph` / `reset_graph` —— TradingAgentsGraph singleton（初始化代价高）
  - `_extract_trend` —— 启发式从 `market_report` 提取趋势（上行/下行/震荡）
  - `_to_report` —— 把 final_state + decision_raw + meta 映射为我们的 `Report` schema
- ✅ `src/trading_agent_service/analysis/adapter.py`
  - 把原 placeholder 替换为 lazy-load 真实 adapter
  - factory 升级：`prefer="tradingagents"` 不可用时**抛错**（不再静默 fallback 到 mock，便于发现问题）
- ✅ `scripts/smoke_test_phase3.py` —— 8 个映射逻辑测试，**不发真 LLM**

## 架构

```
┌──────────────────────── ai-closed-loop-lab ─────────────────────────┐
│                                                                       │
│  TradingAgent Service (FastAPI :8001)                                 │
│         │                                                              │
│         ▼                                                              │
│  cache_manager  ───►  get_analyzer(prefer="tradingagents")            │
│                                  │                                     │
│                                  ▼                                     │
│                   RealTradingAgentsAdapter                             │
│                         │                                              │
│              ┌──────────┴──────────┐                                  │
│              ▼                      ▼                                  │
│      _load_stock_meta         _get_graph (singleton)                  │
│         (akshare)                 │                                    │
│                                   ▼                                    │
└───────────────────────────────────┼──────────────────────────────────┘
                                    │
                                    ▼
                  ┌─────── tradingAgents_neo ────────┐
                  │  TradingAgentsGraph              │
                  │   ├─ Market Analyst              │
                  │   ├─ News / Sentiment / Funds    │
                  │   ├─ Bull vs Bear debate         │
                  │   ├─ Trader plan                 │
                  │   └─ Risk debate → final         │
                  │  (config: market="cn", akshare)  │
                  └──────────────────────────────────┘
```

## 字段映射

| Report 字段 | 来源 |
|------------|------|
| `symbol` | 调用方传入 |
| `name` | akshare snapshot |
| `current_price` | akshare snapshot |
| `summary` | `final_state["final_trade_decision"]` (前 400 字) |
| `technical.trend` | `_extract_trend(market_report)` 启发式 |
| `technical.summary` | `final_state["market_report"]` (前 800 字) |
| `technical.key_levels` | `current_price ± 5%`（占位） |
| `fundamental.industry` | akshare |
| `fundamental.pe_ttm / pb / market_cap_yi` | akshare |
| `fundamental.summary` | `final_state["fundamentals_report"]` (前 800 字) |
| `bull_case` | `final_state.investment_debate_state.bull_history` |
| `bear_case` | `final_state.investment_debate_state.bear_history` |
| `final_decision` | `process_signal()` 返回 → `_normalize_decision()` |
| `confidence` | 决策映射：BUY/SELL=0.70, OVERWEIGHT/UNDERWEIGHT=0.62, HOLD=0.50 |
| `reevaluation_price_range` | `current_price ± 5%`（缓存失效边界） |
| `valid_until` | today + (3 if deep else 1) |

## 配置

### 必需 env

```bash
export OPENAI_API_KEY=sk-xxx              # tradingagents 用
export OPENAI_BASE_URL=https://api.openai.com/v1  # 可选，自定义入口
```

### 可选 env（控制行为）

| 变量 | 默认 | 说明 |
|------|------|------|
| `TAS_ANALYZER` | `auto` | `auto` / `mock` / `tradingagents` |
| `TAS_TA_MARKET` | `cn` | A 股 `cn`，美股 `us` |
| `TAS_TA_DEEP_MODEL` | `gpt-5.3-chat` | 深度思考模型 |
| `TAS_TA_QUICK_MODEL` | `gpt-5.3-chat` | 快速模型（信号提取等） |
| `TAS_TA_DEBATE_ROUNDS` | `1` | 多空辩论轮数（增加耗时和 token） |
| `TAS_TA_RISK_ROUNDS` | `1` | 风险讨论轮数 |
| `TAS_TA_OUTPUT_LANG` | `Chinese` | 报告语言 |

## 部署步骤

### 1. 安装 tradingagents 包

```bash
conda activate ai-lab
pip install -e /Users/neo/Projects/tradingAgents_neo
```

> 这会顺带装 backtrader / langgraph / langchain-google-genai 等一堆。
> 第一次装预计 1-3 分钟。

### 2. 配置 API Key

`/Users/neo/Projects/ai-closed-loop-lab/.env` 加：
```bash
OPENAI_API_KEY=sk-xxx
TAS_ANALYZER=tradingagents
TAS_TA_MARKET=cn
TAS_TA_DEBATE_ROUNDS=1
```

### 3. 跑单元测试（不发真 LLM）

```bash
python scripts/smoke_test_phase3.py
```

预期 8/8 passed，全部 < 1 秒。

### 4. 跑端到端

```bash
TAS_ANALYZER=tradingagents python scripts/run_trading_agent_service.py
```

启动日志会出现：
```
[INFO] 使用 RealTradingAgentsAdapter
[INFO] Initializing TradingAgentsGraph: market=cn deep=... rounds=1
```

发请求：
```bash
curl -X POST http://127.0.0.1:8001/analyze \
  -H 'Content-Type: application/json' \
  -d '{"symbol":"600519","depth":"deep"}'
```

⚠️ **首次 propagate 需 30-180 秒**（多 agent 多轮辩论 + 多次 LLM 调用）。
后续相同 symbol 走我们自己的 cache，毫秒级返回。

### 5. 监控成本

```bash
curl http://127.0.0.1:8001/stats
# cache_hit_rate 越高越省钱
```

`~/.tradingagents/logs/` 目录下有每次 propagate 的完整日志，可手动追踪 token 用量。

## 实战验收（2026-04-27 端到端跑通）

### 链路验证

```
akshare 全市场快照 (5846 只 / 496 板块)
    ↓
HotSectorDetector → 半导体设备, 集成电路制造, 其他家电Ⅱ/Ⅲ, 体育Ⅱ
    ↓
RuleEngine (7 条规则) → 30 候选
    ↓
TradingAgent /analyze (top 3，真 Azure OpenAI gpt-5.3-chat)
    ↓
data/daily_picks/2026-04-27.json + data/trading_agent_service/reports/*.json
```

### 关键数据

| 项 | 数值 |
|----|------|
| LLM 提供商 | Azure OpenAI (deployment: `gpt-5.3-chat`) |
| 单只 propagate 耗时 | **6.7-7.5 分钟** |
| 单只 LLM 调用次数 | 12-14 次（含 quick + deep） |
| 三只并发模式 | **串行**（DailyScanPipeline.run 用 for 循环） |
| 3 只 max-agent-calls 总耗时 | **~25 分钟** |
| 单只 propagate 成本（粗估） | $1.5-3（视 prompt 长度） |
| HTTP timeout | 已从 120s 调到 **900s** 覆盖单次慢 LLM |

### TradingAgent 实战判断质量

**今日候选**全是涨停板（+20%）的科创板股票，规则引擎给所有 30 只都打 5.5 分（无区分度），但 TradingAgent **一致判 UNDERWEIGHT**：

| 股票 | 涨幅 | rule_score | agent | 关键论点 |
|------|------|-----------|-------|----------|
| 688381 帝奥微 | +20.01% | 5.5 | SELL (0.62) | 锁定 70% 月涨幅收益，减仓 50-70% |
| 688001 华兴源创 | +20.0% | 5.5 | SELL (0.62) | 高成交+高情绪阶段，降仓 50-70% |
| 688677 海泰新光 | +20.0% | 5.5 | SELL (0.62) | 分批减 40-60%，止损位 105 |

**结论**：今日激进/稳健均 0 推荐，**这是正确答案**——TradingAgent 准确识别出"追涨陷阱"，避免在情绪高潮入场。

### 缓存复用验证

`600519` 此前已生成报告并入缓存。再跑该 symbol：毫秒级直接返回，无 LLM 调用，证明 `cache_manager` 工作正常。

## 已知限制 & Phase 4 待办

### 性能（实测发现的瓶颈）

1. **`_load_stock_meta` 太慢**——目前从 akshare 拉 5846 只全市场快照才能抽出 1 只的 quote/PE/PB。改进：单独打 `ak.stock_zh_a_spot_em` 单股接口，预计省 1-2 分钟/请求。
2. **propagate 串行**——3 只走 25 分钟。改进：开 graph pool（多实例 + 锁），或者把 `_enrich_with_agent` 改为 `httpx.AsyncClient` + `asyncio.gather`。理论上 3 只并发 ≈ 7 分钟（瓶颈是单只 propagate）。
3. **rule_score 维度单一**——今日所有热门板块涨停股全是 5.5，无区分度。Phase 4 加：RSI 偏离、距高点回撤、分时量价背离 等微观因子。
4. **激进/稳健入选条件偏严**——Phase 4 可改为「rule_score >= 5 且 agent BUY/HOLD」入稳健，让 HOLD 也有展示位。

### 体验

| 项 | 现状 | 改进方向 |
|----|------|---------|
| Token 成本统计 | 没有打点 | 给 graph 注入 LangChain callback 记录 token |
| 失败重试 | propagate 失败直接抛 | tenacity 包一层 + 超时 |
| 并发 | singleton + 锁，只能串行 | 池化多个 graph 实例 |
| current_price 时效 | snapshot 在 ai-lab 端缓存 5 分钟 | 单独打 akshare 拿 realtime |
| key_levels 占位 | 用 ±5% 替代 | 让 market analyst 显式输出支撑/阻力 |
| 反思学习 | `reflect_and_remember` 没接 | Phase 4 配合复盘归因接入 |

## 故障排查

### `tradingagents 不可用`
```bash
python -c "import tradingagents; print(tradingagents.__file__)"
# 如果 ImportError → pip install -e /Users/neo/Projects/tradingAgents_neo
```

### LLM API 401
检查 `.env`：
```bash
echo $OPENAI_API_KEY      # 应非空
echo $OPENAI_BASE_URL     # 默认值或自定义
```

### akshare 拉数据失败
```
[WARNING] akshare meta load failed for 600519: ...
```
影响：`current_price=0`、行业/PE 字段为空。LLM 部分仍然能跑（TradingAgents 内部也通过 akshare 拉数据，可能也会失败）。
处理：等 akshare 接口恢复，或先用 `TAS_ANALYZER=mock` 测流水线。

### 单次调用 > 5 分钟
```bash
# 降辩论轮数
export TAS_TA_DEBATE_ROUNDS=1
export TAS_TA_RISK_ROUNDS=1

# 或换更快的模型
export TAS_TA_DEEP_MODEL=gpt-4o-mini
export TAS_TA_QUICK_MODEL=gpt-4o-mini
```
