# Phase 1 — P0 基础能力（已完成）

> 完成日期：2026-04-26
> 对应需求文档：[requirements.md](./requirements.md) §5 P0 项

## 交付清单

### 1. TradingAgent HTTP 服务 (`src/trading_agent_service/`)
- ✅ FastAPI 应用：`/analyze` `/report/{symbol}` `/health` `/stats`
- ✅ Pydantic Schemas：`Report` 含 `reevaluation_price_range` + `valid_until`
- ✅ Cache 层（SQLite 元数据 + JSON 全文）：
  - 缓存键 `(symbol, evaluated_date)`
  - 失效判定：当前价超出区间 / 报告过期 / `force_refresh`
- ✅ Analyzer 适配器：`MockAnalyzer`（已可用）+ `TradingAgentsAdapter`（占位待 Phase 2）
- ⏳ 知识星球同步：留接口，Phase 3 实现

### 2. Stock Analyzer (`src/stock_analyzer/`)
- ✅ AKShare 客户端 + Mock fallback（无网时自动降级）
- ✅ 热点板块识别（涨幅/成交额/主力净流入加权打分）
- ✅ 选股规则引擎：YAML 配置 + 装饰器注册 + 加权投票
- ✅ 7 条内置规则：`not_st` / `market_cap_range` / `in_hot_sector` / `volume_breakout` / `strong_turnover` / `main_fund_inflow` / `reasonable_valuation`
- ✅ 每日选股流水线 `DailyScanPipeline`：行情→热点→规则→Agent→交易员综合→落盘
- ✅ 规则文件：`config/rules.yaml`（人工可编辑、git 版本化）

### 3. Webhook Listener (`src/webhook_listener/`)
- ✅ FastAPI 端点 `/webhook/trade`：接收 `text + image` (multipart)
- ✅ 文字合规：敏感词替换 + 价格区间化 + 违规词拦截
- ✅ 图片脱敏（Phase 1 占位）：底部 1/3 区域高斯模糊
- ✅ SQLite 落库 `trade_records`
- ⏳ 推送事件到 Central Brain：Phase 2 实现

### 4. 启动脚本（`scripts/`）
- `run_trading_agent_service.py` — 启动 :8001
- `run_webhook_listener.py` — 启动 :8002
- `run_daily_scan.py` — 一次性触发选股

### 5. 测试（`tests/`）
- `test_trading_agent_cache.py` — Cache 命中/失效/价格区间
- `test_rule_engine.py` — 规则引擎 + 内置规则 + YAML 加载
- `test_text_compliance.py` — 敏感词/价格/违规词

## 验收步骤

### A. 安装依赖

```bash
cd /Users/neo/Projects/ai-closed-loop-lab
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### B. 跑单元测试

```bash
pytest -v
```

预期：3 个测试文件、~12 个用例全绿。

### C. 启动 TradingAgent 服务

```bash
# Terminal 1
TAS_ANALYZER=mock python scripts/run_trading_agent_service.py
# 监听 http://127.0.0.1:8001
```

测试调用：
```bash
curl http://127.0.0.1:8001/health
curl -X POST http://127.0.0.1:8001/analyze \
  -H "Content-Type: application/json" \
  -d '{"symbol":"600519","depth":"deep"}'

# 第二次请求应命中缓存（cache_hit=true，elapsed<0.01s）
curl -X POST http://127.0.0.1:8001/analyze \
  -H "Content-Type: application/json" \
  -d '{"symbol":"600519"}'

curl http://127.0.0.1:8001/stats
```

### D. 跑每日选股流水线（与 Agent 服务联动）

```bash
# Terminal 2（保持 Agent 服务运行）
python scripts/run_daily_scan.py
```

预期：
- 输出 `🚀 激进推荐 1-3 只` + `🛡  稳健推荐 1-3 只`
- 结果落盘 `data/daily_picks/YYYY-MM-DD.json`
- TradingAgent `/stats` 显示 `cached_reports` 增加

### E. 启动 Webhook Listener + 测试合规处理

```bash
# Terminal 3
python scripts/run_webhook_listener.py
```

测试：
```bash
# 纯文字
curl -X POST http://127.0.0.1:8002/webhook/trade \
  -F "text=今天买入贵州茅台，建仓位置1500元" \
  -F "source=manual"
# 预期：is_publishable=true，"买入"→"上车"、"建仓"→"关注"、"1500元"→"1500元附近"

# 含违规词
curl -X POST http://127.0.0.1:8002/webhook/trade \
  -F "text=这只股票必涨"
# 预期：is_publishable=false，forbidden_hits=["必涨"]

# 文字 + 图片
curl -X POST http://127.0.0.1:8002/webhook/trade \
  -F "text=持仓截图" \
  -F "image=@/path/to/screenshot.png"
# 预期：raw_image_path + redacted_image_path 都返回，redacted 文件已模糊

curl http://127.0.0.1:8002/webhook/records/recent
```

## 已知 Phase 1 限制

| 项 | 当前 | Phase 2/3 升级 |
|----|------|----------------|
| TradingAgent 真实分析 | MockAnalyzer | 接入 `tradingagents_neo` |
| AKShare 拉取 | 失败自动 mock | 加重试 + 缓存 + 多数据源融合 |
| 图片脱敏 | 底部1/3整体模糊 | OCR 自动定位 6 位代码/中文名后局部模糊 |
| 知识星球 | 无 | API/浏览器自动化（待方案确认） |
| 事件总线 | 直接调用 | 接入 `central_brain` EventBus |
| 限流 | 无 | API 加 SlowAPI middleware |
| 服务守护 | 手动启动 | systemd / launchd |

## 与 v0.1 代码的关系

- `src/agents/{explorer,strategist,executioner,influencer}/` —— 旧四 Agent 簇代码**保留**，作为 LangGraph 编排参考
- 新代码 `src/{trading_agent_service,stock_analyzer,webhook_listener}/` 与旧代码**并列**、互不干扰
- `config/trading.yaml`（旧）vs `config/rules.yaml`（新）—— 后者是 Phase 1 选股规则的唯一真源

## 下一步（Phase 2 候选）

按需求文档 §5 优先级：
1. 把 Stock Analyzer 输出的 `daily_picks` 推送给 Social Media `topic_router`
2. Social Media 接入 Webhook 的 redacted 数据生成笔记草稿
3. TradingAgent 接入真实 `tradingagents_neo`
4. 交易结果复盘（FR-1.3）
