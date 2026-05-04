# 🤖 AI 闭环实验室 (AI Closed Loop Lab)

> **沈经理的交易智能体集群** — 从市场探索到社交传播的全链路 AI 闭环系统。

## 系统架构

```
┌─────────────────────────────────────────────────────────────┐
│                    元数据中心 (Central Brain)                  │
│              SQLite + 向量存储 + 消息总线                        │
└─────────────────────────────────────────────────────────────┘
                              │
        ┌─────────────────────┼─────────────────────┐
        │                     │                     │
        ▼                     ▼                     ▼
┌──────────────┐    ┌──────────────┐    ┌──────────────┐
│  探索者 Agent │───▶│  决策者 Agent │───▶│  执行者 Agent │
│  (Explorer)  │    │ (Strategist) │    │ (Executioner)│
└──────────────┘    └──────────────┘    └──────────────┘
        │                     │                     │
        └─────────────────────┼─────────────────────┘
                              │
                              ▼
                    ┌──────────────┐
                    │ 社交媒体 Agent│
                    │ (Influencer) │
                    └──────────────┘
                              │
                              ▼
                    ┌──────────────┐
                    │  反馈循环      │
                    │(Feedback Loop)│
                    └──────────────┘
```

## 四大 Agent 簇

| Agent | 职责 | 技术栈 | 状态产出 |
|-------|------|--------|----------|
| **探索者 (Explorer)** | 全市场扫描，发现猎物 | AkShare + Qlib | `hot_sectors`, `target_stocks` |
| **决策者 (Strategist)** | 深度体检，生成交易信号 | TradingAgents + LLM | `trade_signals` |
| **执行者 (Executioner)** | 盯盘与自动下单 | EasyTrader + WebSocket | `filled_orders` |
| **社交媒体 (Influencer)** | 交易过程流量化 | LangGraph + 小红书 CLI | `published_posts`, `fan_feedback` |

## 数据流闭环

1. **探索** → 每日 15:30 自动扫描，输出 Top 50 候选票 + 热点板块
2. **决策** → 对候选票进行技术分析，生成带止损/目标价的 `TradeSignal`
3. **执行** → 实时盯盘，触发价格时自动下单（模拟盘先行）
4. **传播** → 成交后自动生成文案/图表，发布至社交媒体
5. **反馈** → 收集评论区高质量观点 + 周末复盘，反向优化决策 Prompt

## 快速开始

```bash
# 1. 克隆并进入项目
cd ai-closed-loop-lab

# 2. 创建虚拟环境
python -m venv .venv
source .venv/bin/activate

# 3. 安装依赖
pip install -e ".[dev]"

# 4. 配置环境变量
cp .env.example .env
# 编辑 .env，填入 API Key 和账号信息

# 5. 运行探索者扫描（单机测试）
python -m scripts.run_explorer --mode scan

# 6. 运行完整闭环（模拟盘）
python -m scripts.run_full_loop --mode paper
```

## 项目结构

```
ai-closed-loop-lab/
├── src/
│   ├── graph/
│   │   ├── state.py          # 统一状态定义 (TradingState)
│   │   └── workflow.py       # LangGraph 主工作流编排
│   ├── agents/
│   │   ├── explorer/         # 探索者：市场扫描 + Alpha 预测
│   │   ├── strategist/       # 决策者：信号生成 + 风控
│   │   ├── executioner/      # 执行者：盯盘 + 下单
│   │   └── influencer/       # 社交媒体：素材生成 + 发布
│   ├── central_brain/        # 元数据中心：消息总线 + 持久化
│   ├── feedback_loop/        # 复盘 + Prompt 进化 + 评论反哺
│   └── infra/                # 配置、日志、模型适配
├── config/
│   ├── trading.yaml          # 交易规则配置
│   └── social_accounts.yaml  # 社交媒体账号配置
├── scripts/                  # 独立运行脚本
├── notebooks/                # 研究 Notebook
├── reports/                  # 自动生成的周报/月报
├── docs/
│   └── architecture.md       # 完整架构设计文档
├── data/                     # 运行时数据 (gitignored)
├── third_party/              # 子项目引用
│   ├── tradingAgents_neo/    # 交易分析框架
│   └── social_media_automation/ # 社交媒体自动化
└── pyproject.toml
```

## 子项目引用

- `third_party/tradingAgents_neo` — [TradingAgents](https://github.com/TauricResearch/TradingAgents) 多Agent金融交易框架
- `third_party/social_media_automation` — 基于LangGraph的多人格社交媒体自动化系统

## 📚 文档

> ⚠️ **当前代码实现仍是 v0.1 架构**（四 Agent 簇：Explorer/Strategist/Executioner/Influencer）。
> **v0.2 文档**基于 2026-04-26 需求澄清，重构为三模块架构（股票分析平台 / TradingAgent 服务 / Social Media），代码重构尚未开始。

- [📋 需求文档 v0.2](docs/requirements.md) — 三大模块完整功能需求 + 优先级 + 风险边界
- [🏗 架构设计 v0.2](docs/architecture.md) — 新架构：模块化、服务化、Cache、知识星球同步

## 避坑指南

- **数据隔离**：`Strategist` 和 `Executioner` 必须单机/私有部署，持仓明细和秘钥严禁上传公共Agent平台。
- **模拟盘先行**：前一个月先跑模拟盘，跑通闭环后再上实盘。
- **Qlib 不是神**：A股情绪驱动成分高，`Social-Media-Automation` 抓取的社交情绪数据权重应调高。

## License

MIT — 仅供研究学习，不构成投资建议。
