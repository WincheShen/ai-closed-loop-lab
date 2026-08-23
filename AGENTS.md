# Agent / 开发约定

本文件给后续在本仓库改代码的人（含 Devin）。2026-08-22 Phase 0 审计后生效。
详情见 `docs/phase0_audit.md` 与 `docs/architecture_refactor_and_winrate_plan.md`。

## 生产数据

- **运营真相**是 NAS：`/volume1/docker/ai-lab/data/central_brain.db`（容器 `ai-lab-scheduler`）。
- 本机 `data/central_brain.db` 已与生产分叉，禁止当作复盘/胜率依据。
- `/volume1/docker/ai-lab-web/` 是 5 月残留，不要往那里写。

## 冻结（违反即视为做错方向）

1. 禁止向 `src/ai_platform/` 新增功能（EventBus、workflow_engine、strategy_metrics、content_ai 新入口都不加）。
2. 禁止新增 SQLite 文件。生产写入只允许 `central_brain.db`。
3. 禁止再往 `src/agents/strategist/signal_generator.py` 的 Prompt 里加新策略名。新策略只允许加检测器函数。
4. 禁止拆 MemoryStore、上 Redis、扩大实盘资金。自动回写 `trading_persona.yaml` / `prompt_weights.json` 默认关闭（需显式 env）。
5. 新开仓硬闸：`data_quality` 为 mock/degraded/unavailable 时 Strategist 必须空转。
6. `SIGNAL_FACTORY=1` 才走检测器出信号；默认仍走旧 LLM 生成。至少 1 短线 + 1 价值过 `G-bt-*` 才允许打开。
7. 生产库在 NAS `/volume1/docker/ai-lab/`；本机库已分叉。

## 部署

- 热更新：`./scripts/deploy_nas.sh`（`COPYFILE_DISABLE=1 tar`，远端清 `._*`，compose up，不重建镜像）。
- 禁止往 `/volume1/docker/ai-lab-web/` 部署。
- mock regime 沿用真实快照最多 2 个交易日，超时强制 `defend` + `data_quality=unavailable`。
- 调度器自愈：NAS cron 每 5 分钟跑 `scripts/scheduler_watchdog.py`；心跳超时 10 分钟重启 `ai-lab-scheduler`。
- 告警：`OPS_WEBHOOK_URL` 填企业微信/飞书/钉钉/Bark；空则读 `GET /api/ops/alerts` 或 `logs/ops_alerts.log`。

## 验证

- 策略对错先走 `python -m src.strategy_lab --strategy <id> --start 2024-01-01`（有缓存才算研究证据；`--synthetic` 只验引擎）。
- 硬规则（止损、禁策略、数据降级禁开仓、信号过期）必须是代码，不经过 LLM。
- 新开仓必须带归一化 `strategy_id`；平仓归因读 `positions.strategy_id`，禁止再把中文策略名当 id。
