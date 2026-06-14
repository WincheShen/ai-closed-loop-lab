# AI Trading Agent System Architecture

This document describes the **current runtime architecture**, **scheduled automation**, and **data flow** of the AI trading system based on the codebase.

The system is an **event‑driven multi‑agent trading platform** that performs market analysis, generates trade signals, executes decisions, produces reports, and feeds results into a feedback loop.

Core ideas behind the system:

- Event‑driven agents
- Automated daily pipeline
- Explainable trading decisions
- Continuous feedback and optimization

---

# 1. System Overview

The platform consists of the following major layers:

1. Scheduler & Automation
2. Market Data Layer
3. Strategy & Analysis Engine
4. Agent Workflow Pipeline
5. Event Bus
6. Storage & System Memory
7. Strategy Feedback System
8. API Layer
9. Frontend Dashboard
10. Content / Social Media Generation

High‑level runtime flow:

Scheduler  
→ AI Pipeline  
→ EventBus  
→ Trading Decisions  
→ Databases  
→ Daily Report API  
→ Frontend Dashboard

Parallel flow:

Trading Events  
→ EventBus  
→ Content Agents  
→ Social Media Dispatcher

---

# 2. Scheduler and Automation

Main entry point:

scripts/scheduler.py

The scheduler runs continuously using the **schedule** library.

Startup:

python scripts/scheduler.py

Main loop behavior:

- check scheduled jobs
- execute due tasks
- emit heartbeat every 5 minutes
- sleep 30 seconds

This process is intended to run as:

- systemd service
- Docker container
- background daemon

---

# 3. Daily Task Schedule

The scheduler orchestrates the entire trading lifecycle.

## Morning Regime Detection

09:35

Task:

MarketBrain snapshot

Function:

job_market_brain_only()

Purpose:

Evaluate the market environment.

Outputs:

- market regime
- recommended posture
- maximum total portfolio exposure
- hot sectors

Stored in:

market_regime_snapshots

---

## Intraday Monitoring

Every 30 minutes  
09:30 → 14:30

Task:

Intraday position review

Function:

job_intraday_review()

Calls:

run_intraday_review()

Purpose:

Reevaluate open positions.

Possible outcomes:

BUY  
SELL  
HOLD

Only actions different from HOLD are logged.

---

## Midday Regime Update

11:35

Task:

MarketBrain snapshot

Purpose:

Detect regime drift during trading hours.

---

## Afternoon Regime Update

14:00

Task:

MarketBrain snapshot

Purpose:

Final regime reassessment before market close.

---

# 4. End-of-Day Pipeline

Most of the AI system runs after market close.

---

## 15:05 Closing Analysis

Function:

job_closing_analysis()

Runs:

run_closing_analysis()

Purpose:

- summarize trading activity
- analyze positions
- produce narrative insights
- generate social content drafts

---

## 15:15 Trade Attribution Backfill

Function:

job_backfill_attributions()

Purpose:

Ensure every closed trade has attribution.

Steps:

1. find positions with status = closed
2. check if attribution exists
3. generate attribution if missing

Component:

TradeAttributor

Output stored in:

trade_attributions

---

## 15:35 Full AI Pipeline

Function:

job_daily_pipeline()

Runs:

run_daily_pipeline("mock")

This is the **core automated trading workflow**.

Pipeline stages:

MarketBrain  
Explorer  
Strategist  
RiskGovernor  
Executioner  
Influencer

---

# 5. Agent Workflow Pipeline

## MarketBrain

Determines the market regime.

Outputs include:

- regime classification
- risk appetite
- recommended posture
- dominant styles
- avoid styles
- hot sectors

Used by downstream agents.

---

## Explorer

Scans the entire market.

Uses:

AkshareClient  
HotSectorDetector

Produces:

candidate stocks

These form the **initial stock universe**.

---

## Strategist

Evaluates candidates.

Produces:

TradeSignal objects.

Each signal contains:

symbol  
entry_price  
target_price  
stop_loss  
position_pct  
strategy  
rationale

---

## RiskGovernor

Evaluates risk of each signal.

Possible outcomes:

approve  
reduce  
reject

Outputs:

RiskDecision

Fields include:

approved_position_pct  
risk_flags  
decision_reason

---

## Executioner

Executes approved signals.

Produces:

orders.

Stored in database tables.

Fields include:

order_id  
symbol  
quantity  
avg_price  
fees  
status

---

## Influencer

Generates narrative insights and content.

This connects the trading system with the content publishing system.

---

# 6. Market Data Layer

Primary module:

src/stock_analyzer/data_source/akshare_client.py

Provides unified market data access.

Core data models:

StockQuote  
SectorQuote  
MarketSnapshot  
KlineBar

Data source priority:

1 AKShare  
2 Sina Finance API  
3 Mock data fallback

This ensures the pipeline continues working even when external APIs fail.

---

# 7. Hot Sector Detection

Component:

HotSectorDetector

File:

src/stock_analyzer/data_source/hot_sector_detector.py

Input:

MarketSnapshot

Scoring formula:

score =
0.5 * change_pct  
+ 0.2 * turnover  
+ 0.3 * main_fund_net_inflow

Output:

Top ranked sectors.

Used by:

MarketBrain  
Explorer

---

# 8. Event Bus System

Core file:

src/ai_platform/central_brain/event_bus/event_bus.py

The EventBus provides system‑wide communication between agents.

Features:

- publish / subscribe model
- SQLite event logging
- optional Redis stream implementation

Event structure:

event_type  
payload  
timestamp

Example events:

daily.picks.generated  
trade.record.created

Subscribers include:

TopicGeneratorAgent  
StrategyFeedbackAgent

---

# 9. Strategy Feedback System

Component:

StrategyFeedbackAgent

File:

src/ai_platform/feedback_system/strategy_optimizer/strategy_feedback_agent.py

Purpose:

Collect trading events for later analysis.

Stored in:

data/strategy_feedback/strategy_metrics.sqlite

Table:

strategy_events

Captured events include:

daily.picks.generated  
trade.record.created

---

# 10. Strategy Analyzer

File:

src/ai_platform/feedback_system/strategy_optimizer/strategy_analyzer.py

Provides analytics on recorded strategy events.

Outputs include:

total_events  
total_picks  
total_trades

Derived metric:

picks_to_trades_ratio

This feeds future optimization systems.

---

# 11. Content Generation Agent

Component:

TopicGeneratorAgent

File:

src/ai_platform/content_ai/topic_generation/topic_generator_agent.py

Purpose:

Convert trading signals into social media topics.

Triggered by event:

daily.picks.generated

Workflow:

DailyPicks  
→ TopicRouter  
→ SmaClient.dispatch()

The payload is sent to:

social_media_dispatcher

---

# 12. API Layer

Trading analysis API models are defined in:

src/trading_agent_service/api/schemas.py

Main request:

AnalyzeRequest

Main response:

AnalyzeResponse

Response includes:

technical analysis  
fundamental analysis  
bull/bear debate  
final decision  
confidence score

These APIs power the frontend analysis tools.

---

# 13. Frontend Dashboard

Primary page:

frontend/src/pages/AgentDailyReport.tsx

Displays the daily system report.

Data endpoints:

GET /api/agent-report/dates  
GET /api/agent-report/{date}

Report sections:

Market Regime  
Stock Picks  
Trade Signals  
Risk Decisions  
Orders  
Trade Attribution

This page visualizes the **entire AI decision pipeline**.

---

# 14. End‑to‑End Data Flow

Full lifecycle of a trading day:

Market Data  
→ Market Snapshot  
→ Hot Sector Detection  
→ MarketBrain Regime Analysis  
→ Explorer Market Scan  
→ Strategist Signal Generation  
→ RiskGovernor Risk Review  
→ Executioner Trade Execution  
→ Trade Attribution  
→ Strategy Feedback Logging  
→ Daily Report API  
→ Frontend Dashboard

Parallel event pipeline:

Agent Pipeline  
→ EventBus.publish()  
→ TopicGeneratorAgent  
→ StrategyFeedbackAgent  
→ Social Media Dispatcher

---

# 15. System Design Principles

Event‑Driven Architecture

Agents communicate via events rather than direct dependencies.

Fault Tolerance

Multiple market data fallbacks ensure reliability.

Automated Workflow

The scheduler guarantees consistent daily operation.

Explainability

Each stage outputs structured data visible in reports.

Continuous Learning

Trade attribution and feedback data support strategy evolution.

---

# 16. Future Extensions

Planned improvements include:

Redis‑based distributed event bus

Broker API integration

Automatic social media publishing

LLM‑driven strategy optimization

Engagement analytics for content

Portfolio‑level reinforcement learning

Real‑time intraday agent orchestration
