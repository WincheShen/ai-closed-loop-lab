# Phase 2 — 跨仓库联动（ai-lab ↔ Social-media-automation）

> 完成日期：2026-04-26
> 对应需求：[requirements.md](./requirements.md) §5 P0 — Social Media 选题路由 / 创作引擎接入

## 交付清单

### ai-closed-loop-lab 侧
- ✅ `src/social_media_dispatcher/`
  - `schemas.py`：`TopicPayload` / `TopicContext` / `StockBriefForSMA` / `TradeRecordBrief`
  - `topic_router.py`：从 `DailyPicks` / 交易记录 / 人工文本组装 payload，**带脱敏**
  - `client.py`：HTTP 客户端推送到 SMA
- ✅ `DailyScanPipeline` 流水线末尾自动触发推送（仅当 `--sma-account` 指定）
- ✅ CLI：`scripts/dispatch_to_sma.py`（`from-picks` / `manual` / `health`）
- ✅ 测试：`tests/test_topic_router.py`（脱敏不泄漏 / 三种来源）

### Social-media-automation 侧
- ✅ 新模块 `src/api/`（optional extras `[api]`，默认不装 fastapi）
  - `schemas.py`：与 lab 端 `TopicPayload` 字段对齐的 Pydantic 模型
  - `server.py`：`POST /api/tasks` / `GET /api/tasks/{id}` / `GET /health`
  - 收到 topic 后调用现有 `task_creator.create_task()` + 异步触发 LangGraph
- ✅ 启动脚本：`scripts/run_api_server.py`（默认 `:8003`）

## 架构

```
[ai-lab Stock Analyzer]
        │
        ├── daily_scan ──► DailyPicks (脱敏前)
        │                       │
        │                       ▼
        │                 [TopicRouter] ──► TopicPayload (脱敏后)
        │                                       │
        │                                       │ HTTP POST
        │                                       ▼
        └── webhook ──► trade_record ──┐  [SMA :8003 /api/tasks]
                                        │       │
                                        │       ▼
                                        │  task_creator.create_task
                                        │       │
                                        │       ▼
                                        │  LangGraph: analyst → research
                                        │   → creative → safety → review
                                        │   → execute → monitor → feedback
                                        ▼
                                  (统一入口)
```

## 合规设计

`TopicRouter` 在组装 payload 时强制脱敏：
| 字段 | 脱敏方式 |
|------|---------|
| 股票代码 `600519` | `60xxxx`（保留前 2 位） |
| 股票名称 `贵州茅台` | `贵X台`（首字+X+末字） |
| 精确买入价 `19.85` | 已在上游 `sanitize_text` 改为 `20元附近` |
| 仓位/账户金额 | 不传输 |
| 推荐理由 | 已在 webhook 处过 `sanitize_text` |

测试验证：`test_topic_router.py::test_from_daily_picks_full` 检查 payload 的 JSON 序列化里**不含原代码、不含原名**。

## 验收步骤

### 准备：启动三个服务

终端 A — TradingAgent：
```bash
conda activate ai-lab
cd /Users/neo/Projects/ai-closed-loop-lab
TAS_ANALYZER=mock python scripts/run_trading_agent_service.py
```

终端 B — SMA API（首次需 `pip install -e ".[api]"`）：
```bash
conda activate <sma-env>
cd /Users/neo/Projects/Social-media-automation
python scripts/run_api_server.py
```

### 1. 健康联通

```bash
# 终端 C
conda activate ai-lab
cd /Users/neo/Projects/ai-closed-loop-lab
python scripts/dispatch_to_sma.py health
# 预期：{"status":"ok","accounts":["XHS_01",...]}
```

### 2. 端到端：选股 → 推送 → 创作

```bash
python scripts/run_daily_scan.py --sma-account XHS_01
```

预期日志末尾：
```
[INFO] daily picks saved → data/daily_picks/2026-04-26.json
[INFO] SMA dispatch OK: account=XHS_01 task_id=xxxxxxxx
```

### 3. SMA 端查看任务

```bash
curl http://127.0.0.1:8003/api/tasks?account_id=XHS_01&limit=5 | python -m json.tool
curl http://127.0.0.1:8003/api/tasks/<task_id>             | python -m json.tool
```

LangGraph 跑完后 `draft_title` / `draft_content` 字段会被填充。

### 4. 二次加工模式（人工选题）

```bash
python scripts/dispatch_to_sma.py manual \
  --account XHS_01 \
  --text "聊聊低空经济板块的中线机会"
```

### 5. 单元测试

```bash
pytest tests/test_topic_router.py -v
```

预期：5 个用例全绿，重点 `test_from_daily_picks_full` 验证不泄漏。

## 已知限制

| 项 | Phase 2 现状 | Phase 3 升级 |
|----|--------------|--------------|
| Trade record → SMA | 已有 router，未挂到 webhook 自动触发 | 在 `webhook_listener` 收到记录时自动 dispatch |
| 图片素材 | `redacted_image_url` 字段存在但 SMA 还没消费 | SMA 创作节点接入 redacted 图作为素材 |
| 失败重试 | 无 | 加 `tenacity` 重试 + 死信队列 |
| 鉴权 | 仅 Bearer token 占位 | mTLS / IP allowlist |
| 反向通道 | SMA 无法回调 lab | SMA → lab `/feedback` 上传发布结果 |

## 下一步（Phase 3 候选）

1. 把 webhook 收到沈经理交易记录后**自动**触发 dispatcher（trade_record kind）
2. 交易结果复盘归因（FR-1.3）：从 SMA 拉取 published_post 数据 + 持仓变化做 attribution
3. 知识星球同步：TradingAgent 输出 ↔ 知识星球 API
4. 接入真实 `tradingagents_neo`（替换 MockAnalyzer）
