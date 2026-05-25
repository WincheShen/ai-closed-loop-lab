#!/usr/bin/env bash

# AI Closed Loop Lab — 一键启动（本地开发）
#
# 用法:
#   ./scripts/start_all.sh          # 启动后端 + 前端
#   ./scripts/start_all.sh --backend-only   # 只启动后端服务
#
# 服务端口:
#   8001  TradingAgent Service (mock 模式)
#   8002  Webhook Listener + Strategy API + Agent Report API
#   5173  React 前端 (Vite dev server, 代理到 8002)
#
# 停止: Ctrl+C

set -e

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

export PYTHONPATH="$PROJECT_ROOT/src"

BACKEND_ONLY=false
[[ "$1" == "--backend-only" ]] && BACKEND_ONLY=true

PIDS=()

cleanup() {
    echo ""
    echo "Stopping all services..."
    for pid in "${PIDS[@]}"; do
        kill "$pid" 2>/dev/null
    done
    wait 2>/dev/null
    echo "Done."
}
trap cleanup INT TERM

echo "╔══════════════════════════════════════════╗"
echo "║    AI Closed Loop Lab — Local Dev       ║"
echo "╚══════════════════════════════════════════╝"
echo ""

# ---- Webhook Listener (核心服务, 含 Strategy + Agent Report API) ----
echo "[1/3] Webhook Listener + API  →  :8002"
python scripts/run_webhook_listener.py &
PIDS+=($!)
sleep 1

# ---- TradingAgent Service ----
echo "[2/3] TradingAgent Service    →  :8001"
TAS_ANALYZER=${TAS_ANALYZER:-mock} python scripts/run_trading_agent_service.py &
PIDS+=($!)
sleep 1

# ---- Frontend (Vite dev server) ----
if [ "$BACKEND_ONLY" = false ]; then
    echo "[3/3] React Frontend (Vite)   →  :5173"
    cd "$PROJECT_ROOT/frontend"
    npm run dev -- --host 2>/dev/null &
    PIDS+=($!)
    cd "$PROJECT_ROOT"
else
    echo "[3/3] Frontend skipped (--backend-only)"
fi

echo ""
echo "════════════════════════════════════════════"
echo "  Services running:"
echo "    Frontend:   http://localhost:5173"
echo "    Backend:    http://localhost:8002"
echo "    Agent API:  http://localhost:8001"
echo ""
echo "  Quick actions:"
echo "    Run daily scan:  python scripts/run_daily_scan.py"
echo "    Run full loop:   python scripts/run_daily_workflow.py"
echo "════════════════════════════════════════════"
echo ""
echo "Press Ctrl+C to stop all services."
echo ""

wait
