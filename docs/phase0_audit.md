# Phase 0 审计报告

> 日期：2026-08-22（周六）
> 审计人：总架构师
> 对照：`docs/architecture_refactor_and_winrate_plan.md` §Phase 0
> 结论口径：**以 NAS 生产库为准**。本地 `data/central_brain.db` 是分叉副本，不能当运营真相。

---

## 0. 数据源与部署拓扑

| 位置 | 路径 | 体积 / mtime | 角色 |
|------|------|----------------|------|
| **NAS 生产** | `/volume1/docker/ai-lab/data/central_brain.db` | 228MB，2026-08-20 15:38（WAL 15:40） | **唯一运营真相** |
| NAS 心跳 | `/volume1/docker/ai-lab/data/scheduler.heartbeat` | 0 字节，**2026-08-21 04:55** | 已死亡（审计时已 35h+） |
| NAS 僵尸目录 | `/volume1/docker/ai-lab-web/data/central_brain.db` | 912KB，**2026-05-25** | 旧 webhook 部署残留 |
| 本机开发库 | `data/central_brain.db` | 110MB，WAL 2026-08-21 21:21 | **与 NAS 分叉**，含已禁策略成交 |
| 本机心跳 | `data/scheduler.heartbeat` | 0 字节，2026-07-23 13:48 | 本机 scheduler 早停 |
| 本机 scheduler.log | 项目根 `scheduler.log` | 停在 2026-07-23 盘中复审 | 本机不是生产调度 |

NAS 可达：`192.168.3.73:8002` 返回 `{"status":"ok","service":"webhook_listener"}`。
容器名从残留进程可确认为 `ai-lab-scheduler`。无 sudo 无法 `docker ps`；从心跳/日志已能判定调度进程 **2026-08-21 04:55 之后无输出**。

**写库纪律（立即生效）：** 一切胜率、持仓、复盘数字引用 NAS 生产库。本机库只可用于离线开发，禁止再往本机库当「实盘复盘」用。合并两库不在 Phase 0，列入风险。

---

## 1. 能力对照：「代码有」vs「生产在跑」

| 能力 | 代码 | NAS 生产 | 本机库 | 判定 |
|------|------|----------|--------|------|
| LangGraph 日管线 | `job_daily_pipeline` → `run_daily_pipeline` | 8 月每个交易日 3 个 mock session（三人格），8/3–8/20 **交易日无缺失** | 8 月只零星几天，多在晚上手动 | **生产已生效**；本机不是调度 |
| 30min LLM 复审 | `job_intraday_review` | 8 月每个交易日数十条 `position_reviews`（8/20 仍 79 条 / 12 仓） | **复审停于 7/23**，之后 7 仓 0 复审 | **生产已生效**；本机失效 |
| 硬止损 / -8% | `_pre_llm_hard_check` | 绑在复审上。8 月穿止损降到 1/11，仍非独立扫描 | 7 月穿止损 12/23 | **代码有但没跑成独立硬扫描** |
| pending 5 天过期 | `_expire_stale_pending_signals` | pending=20，全部 ≥8/17，**无 >5 天 pending** | pending 有 7/22、7/31 僵尸 | **生产过期逻辑基本在工作** |
| `active` 信号清理 | 无 | **147 条 active，140 条超过 7 天**（最早 6/15） | 29 条 active 同样堆积 | **缺失：immediate 信号没有过期** |
| 放量突破硬拦截 | `_BANNED_STRATEGY_KEYWORDS` L729 | 最后一条 `2026-07-31 15:36`；8 月 0 入库 | **7/29–7/31 仍 filled**（002498/603459/600418/300244），且变体「放量突破后第一次确认回踩」绕过关键词 | **生产 8 月已生效；变体漏洞仍在；本机库未跟上拦截** |
| 策略名规范化 | `_normalize_strategy_name` | 8 月仍大量 `价值投资/高ROE/...` 长尾名 | 同 | **部分生效，变体仍入库** |
| 热点板块兜底 | emappdata / Sina | 8 月 97 份 regime：`is_mock_data=1` **10 份**，hot 空 **13 份**；8/20 HealthCheck：`success_rate=0%, sina_fallback=229, mock_fallback=7, degraded=True` | 近 60 份热板块非空（本机样本小） | **代码有；生产经常降级且仍在开仓** |
| 底部启动 / 主力吸筹 | Prompt + 规则 | 8 月有主力吸筹成交（含亏损） | 8/18 底部启动 1 笔 | Prompt 在产出，**无回测闸** |
| MiniQMT | `scripts/miniQMT_bridge.py` | 未纳入本审计（Phase 4） | — | 不评价 |
| 健康检查告警 | `job_health_check` 只打日志 | 8/20 16:00 打出 degraded=True，**无 webhook / 无人值守响应** | — | **可见但不可行动** |
| scheduler 常驻 | heartbeat `touch` 每 5min | 8/3–8/20 日间存活；**8/21 04:55 静默死亡**，无 `调度器已停止` | 7/23 已死 | **G-ops-1 当天红灯** |

---

## 2. NAS 生产数字（截至 2026-08-20 收盘）

### 2.1 漏斗

全量信号 330：

| status | n | 备注 |
|--------|---|------|
| active | 147 | **尸堆**，不过期 |
| expired | 121 | |
| filled | 39 | 转化 39/330 = **11.8%**（与 30 天复盘惊人一致） |
| pending | 20 | 均在 5 天窗内 |
| cancelled_position_limit | 3 | 8/17 |

2026-08 信号 144：filled 17（**11.8%**），pending 20，expired 30，active 74，cancelled 3。

8 月下旬条件单开始出现（8/17–20 pending 拉高），转化仍低。价值人格大量 `active` 而未成交——像是风控过了但执行没吃 `immediate`，或同一标的重复发信号。

近 30 日风控：approve 38 / reduce 10 / **reject 218**。拒绝主因：

- 总仓位达 40/50/70% 上限：135
- bear/panic defend/observe：61
- 板块「未知」集中度：18

仓位上限 + 未知板块，说明数据质量和价值人格满仓在互相卡住漏斗。

### 2.2 平仓胜率

| 范围 | n | W/L | 胜率 | 实现盈亏 | 穿止损 |
|------|---|-----|------|----------|--------|
| 全部已平 | 25 | 8/17 | **32.0%** | **¥-23,107** | 7/25 |
| 2026-08 已平 | 11 | 3/8 | **27.3%** | ¥-201 | 1/11 |
| 放量突破（历史） | 9 | 1/8 | 11.1% | ¥-28,052 | 含在全部里 |
| 热点回踩 | 8 | 3/5 | 37.5% | +¥2,453 | |
| 价值投资（各变体合计） | 6 | 4/2 | 66.7% | +¥4,266 | |
| 主力吸筹 | 2 | 0/2 | 0% | ¥-1,698 | |

按人格（全部已平）：

| persona | n | WR | P&L |
|---------|---|----|-----|
| （空 / legacy） | 4 | 25% | -9,757 |
| short_term_hot_rotation_v1 | 15 | 20% | -17,540 |
| duan_yongping_v1 | 3 | 100% | +270 |
| warren_buffett_v1 | 3 | 33% | +3,920（含 603259 +4,196） |

8 月禁掉放量突破后，**净亏损几乎被止血**（-201），但短线新策略（回踩 + 吸筹）合计仍是净亏，胜率 27%。样本仍不够下统计结论，只够说「出血止住了，优势没出现」。

### 2.3 持仓（审计时 13 开仓）

价值仓占绝大多数（段 5 + 巴 4），短线 3 笔（600721 回踩 8/19 REDUCE；301282/301626 吸筹 8/20，**尚无 last_review**——符合「当天开仓未进 14:00 复审」）。

账户 cash：短线 24.0 万 / 段 12.5 万 / 巴 6.8 万（相对 30 万本金）。`total_pnl` 字段全 0，**账户市值未按持仓重估**——看板若读这个字段会骗人。

### 2.4 调度覆盖

- 2026-08-03 ~ 08-20 交易日 session：**无缺失**（每天 3 个人格 mock）。周日 feedback 8/2、8/9、8/16 也有。
- 复审：7/21 缺（当天有 session）、7/25–26 周末；7/27 起恢复且密度正常。
- 最后一次 HealthCheck：2026-08-20 16:00，管线绿、**AKShare degraded=True**。
- 调度器最后心跳：2026-08-21 04:55。之后无 8/21 日志文件。今天是周六，**下个交易日（8/24）若未拉起，将出现开仓无人看管**。

---

## 3. 本机库分叉（不要用来决策）

本机 23 笔平仓 **全部是短线**，胜率 30.4%，P&L **-¥37,262**。热点回踩在本机库是 4W/8L、-¥33,589（300900 -45%），与 NAS「回踩小赚」矛盾——同一策略名、两套成交。

本机 7/29–7/31 仍在把「放量突破」写成 filled，且 8 月仍在手动跑 `daily-20260821-*`。说明开发机连的是本地 SQLite，**拦截补丁可能从未部署进这台进程，或跑的是旧代码**。

Phase 1 起：本机默认 `DB_PATH` 若继续指向 `data/central_brain.db`，开发联调可以，但任何「今日复盘」脚本必须能指向 NAS 或显式副本。

---

## 4. 代码级漏洞（审计时已对照源码）

1. **禁策略只匹配字面 `放量突破` / `volume_breakout`**（`signal_generator.py` L51-52, L731）。`_infer_strategy_id` 不会先跑再拦，故「放量突破后第一次确认回踩」能入库。NAS 7/29 的 002498 即此路径。Phase 1 P0-6 必须改成 **先 normalize / infer strategy_id，再按 id 拦截**。
2. **`run_intraday_review` 在「无持仓」时直接 return，不检查 pending**（`intraday_loop.py` L66-68）。短线空仓时条件单不会被扫。必须把 pending 扫描从「有持仓」前置条件里拆出去（这就是计划里的 `hard_scan`）。
3. **条件单行情失败会落到 mock**（7/23 本机日志：`降级 mock`）。当前 `_check_pending_signals` 写了 `allow_mock_fallback=False`，但 IntradayClient 仍可能返回 `is_mock` 快照；应用侧要显式拒绝 `snapshot.is_mock`。
4. **`active` 状态无 TTL**。147 条僵尸会污染漏斗、经验层样本和看板。
5. **HealthCheck 看见 degraded 仍允许当日已开仓**（闸在扫描层不完整）。8/18–19 有多份 `is_mock_data=1` 的 regime，同时短线仍有成交。
6. **heartbeat 只 `touch`，无 pid/时间戳内容**，也无法在进程外告警。8/21 04:55 死亡无任何 alert。
7. NAS 上残留大量 `docker logs ai-lab-scheduler` 僵尸进程（自 8/16），属于运维事故，可能和调度器死亡有关，Phase 1 拉起前先清。

---

## 5. 冻结约定（Phase 0.3，已生效）

写入 `AGENTS.md`。开发约束：

1. **禁止**向 `src/ai_platform/` 新增功能（含 EventBus、workflow_engine、strategy_metrics）。
2. **禁止**新增 SQLite 文件；生产写入只允许 `central_brain.db`。
3. **禁止**再往 `signal_generator.py` 的 Prompt 里加新策略名。新策略只能加检测器函数。
4. **禁止**把本机 `data/central_brain.db` 的成交当作生产复盘。
5. **禁止**在 Phase 1 完成前拆 MemoryStore / 上 Redis / 接实盘加资金。

---

## 6. 对 Phase 1 的修正（比原计划更准）

原计划假设「复审可能没跑、热点经常空」。NAS 事实是：

| 原假设 | 审计结果 | Phase 1 调整 |
|--------|----------|--------------|
| 复审经常停 | NAS 8 月每天都在复审 | 仍要拆 hard_scan：止损/pending 不能绑 LLM，且空仓要扫 pending |
| 热点经常空 | 8 月 13/97 空，另 10/97 mock | 开仓闸按 `is_mock_data` / degraded **强制**，8/20 已是 degraded 却未停手 |
| 放量突破仍在生成 | NAS 8 月已停；变体与本机库仍有 | P0-6 改成 strategy_id 拦截 + 清理本机误用 |
| pending 68% 空转 | 5 天过期在工作；真正尸堆是 **active×147** | hard_scan 同时 expire `active`（>2 个交易日未成交） |
| scheduler 不稳 | 交易日曾稳，**此刻已死 35h+** | **P0-4 升级为：先拉起 NAS scheduler，再加告警** |

### Phase 1 建议开工顺序（一人）

1. **今天/周日：拉起 `ai-lab-scheduler`**，确认心跳重新跳动，并清掉 8/16 起卡住的 `docker logs` 进程。这是运维动作，不改代码也可以做。
2. P0-1/P0-2：`evaluate_hard_exit` + `hard_scan`（含 pending，即使无持仓）。
3. P0-6：按 `strategy_id` 禁 `volume_breakout`；expire 陈旧 `active`。
4. P0-5：`is_mock_data` / degraded → 禁止新开仓。
5. P0-3/P0-4：node_guard + `notify_ops`（心跳 >10min 必须告警）。

---

## 7. 待你拍板（不挡 Phase 1 代码，但挡「先不拉 NAS」）

1. **是否授权本机用现有 `scripts/ssh_nas.py` 在 NAS 上 `sudo docker restart ai-lab-scheduler`？** 审计时无 TTY，sudo 失败。你也可以自己在 NAS 上重启，然后告诉我心跳时间。
2. 本机库分叉：Phase 1 是否把开发默认 `DB_PATH` 改成只读 NAS 副本，还是维持本地库、仅文档警告？默认建议：**维持本地库开发，脚本加 `--db nas|local`，复盘默认 nas。**

Phase 0 完成定义已满足：审计进 git、冻结写入 `AGENTS.md`、三列能力表齐。
