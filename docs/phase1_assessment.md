# Cognitive Agent Phase 1 评估存档

> 时间：Phase 1 完成后、NAS 实地跑数据前
> 提交：`dff91f8`

## 实现完整性

Phase 1 文档范围完成度约 **90%**：
- TradingPersona 加载 + 版本化 ✅
- MarketBrain 量化 + LLM 综合 ✅
- RiskGovernor approve/reduce/reject ✅
- 新工作流 6 节点 ✅
- 数据库 schema 扩展 ✅
- Strategist 接收 regime context ✅
- Executor 写入认知元数据 ✅

但在"系统能持续自主运转"意义上完整度只有约 **60%**。

## 关键缺口

### 缺口 1：热点检测被瘸（高风险）

- Persona 核心 setup "热点板块前排回踩" 依赖 `hot_sectors`
- 新浪数据源没有板块归属信息，`hot_sectors=[]`
- `config/rules.yaml` 里 `in_hot_sector` (weight=2.0) 永远不命中
- NAS 上 push2.eastmoney 被封，必走 Sina，**最擅长的招数永远用不出来**

### 缺口 2：MarketBrain 只跑一次

- Phase 1 还是 daily snapshot，不是 Agent Kernel 持续循环
- 盘中市场结构变化不会被捕获

### 缺口 3：人格规则静态

- `strategy_regime_compatibility` 是手写猜想，不是从历史数据学到的
- 没有 Phase 2 的归因学习填进来

### 缺口 4：Reviewer / 平仓还是老逻辑

- `intraday_loop.py` / `closing_analysis.py` / `position_reviewer.py` 未改造
- 平仓时不写归因
- 不更新策略表现
- 即使买入了，"为什么亏/赚"没被结构化保存

### 缺口 5：bear 市场可能"永不交易"

- persona yaml 里 hot_sector_pullback + volume_breakout 在 bear/panic 都 forbidden
- 只剩 defensive_bluechip 一条路
- 端到端测试已经出现 30 候选 → 0 信号 → 0 交易
- **对"积累数据"的目标不利**

### 缺口 6：Influencer 完全没动

- 不可能达到日浏览 5000

## 成功概率评估

### 胜率 > 60%（已关闭交易）
- 当前架构能达到的概率：**低，约 20-30%**
- 样本量瓶颈：bear 市场可能整周不交易
- 没有学习闭环
- 数据源缺板块和主力资金

### 日浏览量 > 5000
- 当前架构能达到的概率：**极低，<5%**
- Influencer 没动
- 新账号冷启动需要数月
- 没有选题/标题/算法适配机制

### 综合达标概率

| 阶段 | 综合达标概率 |
|---|---:|
| Phase 1 当前 | <5% |
| Phase 1 + Phase 2 | <5% |
| Phase 1 + Phase 2 + Phase 3 | ~5% |
| 完整 Phase 1-5 | ~20% |
| 完整 Phase 1-5 + 6 个月运营 | ~30% |

**Success metric 是 12 个月级别目标，不是架构能直接拍出来的。**

## NAS 实地跑数据观察清单

每天回收：
1. MarketBrain 判定的 regime 分布
2. Explorer 候选票数量
3. Strategist BUY/PASS 比例
4. RiskGovernor 决策分布
5. 实际交易笔数

一周后回来对照：
- [ ] regime 一周内是否出现过 bull / rebound / neutral
- [ ] "市场弱就不交易" 是合理还是规则太严
- [ ] LLM 的 PASS 理由是否合理
- [ ] 有没有被 reject 的信号其实应该买
- [ ] position 表里认知元数据是否都填了
- [ ] 新浪数据源的字段缺失是否导致规则失效

## 短期建议（NAS 跑数据期间）

降低 persona 严苛度，否则样本荒：

```yaml
strategy_regime_compatibility:
  hot_sector_pullback:
    compatible: [bull, neutral, rebound]
    forbidden: []
    degraded: [bear, panic]
  volume_breakout:
    compatible: [bull, rebound]
    forbidden: [panic]
    degraded: [bear, neutral]
```

风控会减仓但不全拒，能积累样本。

## 后续 Phase 路线

| Phase | 价值 | 状态 |
|---|---|---|
| Phase 2 归因学习 | 决定胜率天花板 | backlog |
| Phase 3 策略实验室 | 决定能否稳过 60% | backlog |
| Phase 4 Social Brain | 5000 浏览唯一路径 | backlog |
| Phase 5 Agent Kernel 事件化 | 从批处理升级到持续循环 | backlog |
| 数据源补全 | 板块/主力资金 | backlog |

## 结论

Phase 1 是一个"会判断市场、会风控、会守纪律"的初级 Agent，
不是一个"会学习、会进化、会运营"的完整 Cognitive Agent。

Phase 1 的工程价值在于：基础设施可观测、可干预、可复盘。
放到 NAS 跑一周积累真实数据，是为 Phase 2 准备燃料的正确决定。
