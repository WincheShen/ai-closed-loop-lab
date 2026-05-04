#!/usr/bin/env bash

# Simple startup script for the AI Closed Loop Lab local stack.
# Starts the core services needed for development.

set -e

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

export PYTHONPATH="$PROJECT_ROOT/src"

echo "Starting AI Closed Loop Lab services..."

# ---- Trading Agent Service ----
echo "[1/5] Starting TradingAgent service on :8001"
TAS_ANALYZER=${TAS_ANALYZER:-mock} python scripts/run_trading_agent_service.py &
PID_TRADING=$!

sleep 1

# ---- Webhook Listener ----
echo "[2/5] Starting Webhook Listener on :8002"
python scripts/run_webhook_listener.py &
PID_WEBHOOK=$!

sleep 1

# ---- Event Monitor ----
echo "[3/5] Starting Event Monitor on :8010"
python scripts/run_event_monitor.py &
PID_MONITOR=$!

sleep 1

# ---- Strategy Metrics API ----
echo "[4/5] Starting Strategy Metrics API on :8011"
python scripts/run_strategy_metrics.py &
PID_METRICS=$!

sleep 1

# ---- Usage Help ----
echo "[5/5] All services started."
echo ""
echo "Trigger daily scan:"
echo "  python scripts/run_daily_workflow.py"
echo ""
echo "View event stream:"
echo "  http://localhost:8010/events/recent"
echo ""
echo "View strategy metrics:"
echo "  http://localhost:8011/strategy/summary"
echo ""

echo "Service PIDs:"
echo "  TradingAgent : $PID_TRADING"
echo "  Webhook      : $PID_WEBHOOK"
echo "  EventMonitor : $PID_MONITOR"
echo "  MetricsAPI   : $PID_METRICS"

echo ""
echo "Press Ctrl+C to stop all services."

trap "echo 'Stopping services...'; kill $PID_TRADING $PID_WEBHOOK $PID_MONITOR $PID_METRICS 2>/dev/null" INT TERM

wait
