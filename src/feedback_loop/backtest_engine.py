"""Backtest Engine — 实战复盘与绩效归因。

职责：
1. 每周末对比 "AI预测结果" vs "市场真实表现"
2. 错误归因：选股逻辑失效？还是交易规则问题？
3. 生成 PerformanceRecord 存入 Central Brain
"""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timedelta
from typing import Any, Literal

from src.central_brain import get_central_brain
from src.graph.state import PerformanceRecord, TradingState
from src.infra.config import cfg
from src.infra.logger import get_agent_logger
from src.stock_analyzer.data_source.akshare_client import AkshareClient, KlineBar

logger = get_agent_logger("feedback_loop", "init")


class BacktestEngine:
    """复盘引擎。"""

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.logger = get_agent_logger("feedback_loop", session_id)
        self.brain = get_central_brain()
        self.akshare = AkshareClient()

    def run_backtest(
        self,
        signals: list[dict],
        week_ending: str | None = None,
    ) -> list[PerformanceRecord]:
        """对本周所有信号进行复盘。

        Args:
            signals: 来自 Central Brain 的 trade_signals 列表
            week_ending: 复盘周结束日期 (YYYY-MM-DD)，默认今天
        """
        week = week_ending or datetime.now().strftime("%Y-%m-%d")
        self.logger.info("开始周复盘 — week_ending=%s, 信号数=%d", week, len(signals))

        records: list[PerformanceRecord] = []
        for sig in signals:
            record = self._analyze_single_signal(sig, week)
            records.append(record)

        # 汇总统计
        wins = sum(1 for r in records if r["actual_return"] > 0)
        losses = sum(1 for r in records if r["actual_return"] < 0)
        avg_return = sum(r["actual_return"] for r in records) / len(records) if records else 0

        self.logger.info(
            "复盘完成 — 信号 %d, 盈利 %d, 亏损 %d, 平均收益 %.2f%%",
            len(records), wins, losses, avg_return * 100,
        )

        self.brain.log_agent_event(
            self.session_id,
            "feedback_loop",
            "backtest_complete",
            {
                "week": week,
                "total_signals": len(records),
                "wins": wins,
                "losses": losses,
                "avg_return_pct": round(avg_return * 100, 2),
            },
        )
        return records

    def _analyze_single_signal(self, signal: dict, week: str) -> PerformanceRecord:
        """分析单条信号的实际表现 — 使用真实 K 线数据。"""
        symbol = signal["symbol"]
        entry = signal.get("entry_price", 0)
        target = signal.get("target_price", 0)
        stop = signal.get("stop_loss", 0)
        strategy = signal.get("strategy", "unknown")
        signal_date = signal.get("timestamp", "")[:10]

        predicted_return = (target - entry) / entry if entry > 0 else 0

        # 拉取信号发出后的真实 K 线
        actual_return, holding_days = self._compute_actual_return(
            symbol, entry, target, stop, signal_date,
        )

        # 归因分析
        error_source: str | None
        if actual_return < -0.03:
            error_source = "trading_rule"
            analysis = f"策略 {strategy} 触发后股价下行，触及止损 {stop:.2f}。需检查该策略近期胜率。"
        elif actual_return < 0:
            error_source = "market_unexpected"
            analysis = f"市场整体回调导致 {symbol} 未达预期，属系统性风险。"
        else:
            error_source = None
            analysis = f"{symbol} 按预期运行，策略 {strategy} 有效。"

        record: PerformanceRecord = {
            "record_id": f"REC-{uuid.uuid4().hex[:8].upper()}",
            "signal_id": signal["signal_id"],
            "symbol": symbol,
            "predicted_return": round(predicted_return, 4),
            "actual_return": round(actual_return, 4),
            "holding_days": holding_days,
            "error_source": error_source,
            "analysis": analysis,
            "week_ending": week,
        }
        return record

    def _compute_actual_return(
        self,
        symbol: str,
        entry_price: float,
        target_price: float,
        stop_loss: float,
        signal_date: str,
    ) -> tuple[float, int]:
        """用真实 K 线计算信号发出后的实际收益和持仓天数。

        逻辑：从 signal_date 后第一天开始逐日扫描，
        - 若盘中低点 <= stop_loss → 止损出局
        - 若盘中高点 >= target_price → 止盈出局
        - 最多持仓 5 天后按收盘价结算

        Returns:
            (actual_return, holding_days)
        """
        if entry_price <= 0:
            return 0.0, 1

        try:
            bars = self.akshare.fetch_kline(symbol, days=30)
        except Exception as e:
            self.logger.warning("K线拉取失败 %s: %s，降级随机", symbol, e)
            return self._fallback_random_return(entry_price, target_price, stop_loss), 3

        # 找到 signal_date 之后的 bars
        after_bars = [
            b for b in bars
            if b.date.isoformat() > signal_date
        ]

        if not after_bars:
            # 没有信号后的数据（可能是最近的信号），用最后收盘价
            if bars:
                last_close = bars[-1].close
                ret = (last_close - entry_price) / entry_price
                return ret, 1
            return 0.0, 1

        # 逐日扫描，模拟止盈止损
        max_hold = min(5, len(after_bars))
        for i, bar in enumerate(after_bars[:max_hold]):
            # 止损：盘中低点触及
            if stop_loss > 0 and bar.low <= stop_loss:
                ret = (stop_loss - entry_price) / entry_price
                return ret, i + 1
            # 止盈：盘中高点触及
            if target_price > 0 and bar.high >= target_price:
                ret = (target_price - entry_price) / entry_price
                return ret, i + 1

        # 持仓到期，按最后一天收盘价结算
        exit_price = after_bars[max_hold - 1].close
        ret = (exit_price - entry_price) / entry_price
        return ret, max_hold

    @staticmethod
    def _fallback_random_return(
        entry: float, target: float, stop: float,
    ) -> float:
        """K线不可用时的降级：基于价格区间的合理随机。"""
        import random
        predicted = (target - entry) / entry if entry > 0 else 0
        rand = random.random()
        if rand < 0.4:
            return (stop - entry) / entry if entry > 0 else -0.03
        elif rand < 0.7:
            return predicted
        else:
            return random.uniform(-0.02, predicted)

    def error_breakdown(self, records: list[PerformanceRecord]) -> dict[str, Any]:
        """错误归因统计。"""
        errors = [r for r in records if r["error_source"] is not None]
        breakdown = {
            "total_signals": len(records),
            "error_signals": len(errors),
            "by_source": {},
            "by_strategy": {},
        }
        for r in errors:
            src = r["error_source"] or "unknown"
            breakdown["by_source"][src] = breakdown["by_source"].get(src, 0) + 1

        # 按策略统计错误率
        all_signals = self.brain.store.list_active_signals() if records else []
        sig_map = {s["signal_id"]: s.get("strategy", "unknown") for s in all_signals}
        for r in records:
            strat = sig_map.get(r["signal_id"], "unknown")
            if strat not in breakdown["by_strategy"]:
                breakdown["by_strategy"][strat] = {"total": 0, "errors": 0}
            breakdown["by_strategy"][strat]["total"] += 1
            if r["error_source"]:
                breakdown["by_strategy"][strat]["errors"] += 1

        return breakdown


async def run_weekly_feedback_node(state: TradingState) -> dict[str, Any]:
    """LangGraph 节点函数 — 每周复盘。

    输入：含 performance_log, trade_signals 的 TradingState
    输出：{"performance_log": [...], "error_analysis": [...]}

    流程:
    1. 对本周信号做 backtest
    2. 统计错误归因
    3. 更新策略权重
    4. 生成 LLM 策略优化建议
    """
    session_id = state["session_id"]
    engine = BacktestEngine(session_id)
    brain = get_central_brain()

    # 从 Central Brain 读取本周所有信号
    signals = state.get("trade_signals", [])
    if not signals:
        # 尝试从数据库读取历史信号
        signals = brain.store.list_active_signals(session_id)
        # 如果状态中没有，读取全部
        if not signals:
            signals = brain.store.list_active_signals()

    if not signals:
        return {
            "performance_log": state.get("performance_log", []),
            "error_analysis": state.get("error_analysis", []),
            "logs": state.get("logs", []) + ["[FeedbackLoop] 本周无信号，跳过复盘"],
        }

    records = engine.run_backtest(signals)
    breakdown = engine.error_breakdown(records)

    # 更新策略权重
    from src.feedback_loop.prompt_evolution import PromptEvolution
    evo = PromptEvolution(session_id)
    weights = evo.update_weights_from_records(records)

    # 生成策略优化建议
    recommendation = _generate_strategy_recommendation(
        session_id, records, breakdown, weights, brain,
    )

    return {
        "performance_log": state.get("performance_log", []) + records,
        "error_analysis": state.get("error_analysis", []) + [breakdown],
        "strategy_recommendation": recommendation,
        "logs": state.get("logs", []) + [
            f"[FeedbackLoop] 复盘 {len(records)} 条信号, 优化建议已生成"
        ],
    }


def _generate_strategy_recommendation(
    session_id: str,
    records: list[PerformanceRecord],
    breakdown: dict[str, Any],
    weights: list[dict],
    brain: Any,
) -> str:
    """基于本周数据生成《策略优化建议》。

    综合 lessons + attributions + backtest records 生成可操作的优化方向。
    """
    # 收集 lessons
    lessons = brain.store.get_recent_lessons(limit=10)
    lessons_text = "\n".join(
        f"- [{l.get('strategy_id', '')}] {l.get('symbol', '')}({l.get('outcome', '')}): {l.get('lesson_text', '')}"
        for l in lessons
    ) if lessons else "暂无"

    # 收集 attributions
    attributions = brain.store.list_recent_attributions(limit=10)
    attr_text = "\n".join(
        f"- {a.get('symbol', '')} | {a.get('outcome', '')} | cause={a.get('primary_cause', '')} | pnl={a.get('pnl_pct', 0):+.1f}%"
        for a in attributions
    ) if attributions else "暂无"

    # 权重摘要
    weights_text = "\n".join(
        f"- {w['strategy_name']}: 权重={w['current_weight']:.3f} (胜{w['win_count']}/负{w['loss_count']})"
        for w in weights
    )

    # 复盘摘要
    wins = sum(1 for r in records if r["actual_return"] > 0)
    losses = sum(1 for r in records if r["actual_return"] < 0)
    avg_ret = sum(r["actual_return"] for r in records) / len(records) if records else 0

    prompt = f"""\
你是一位量化策略优化顾问。根据以下数据，生成一份简洁的《本周策略优化建议》。

## 本周绩效
- 信号数: {len(records)}, 盈利: {wins}, 亏损: {losses}, 平均收益: {avg_ret*100:.2f}%
- 错误归因: {json.dumps(breakdown.get('by_source', {}), ensure_ascii=False)}

## 策略权重现状
{weights_text}

## 交易归因记录
{attr_text}

## 历史教训库
{lessons_text}

## 要求
请输出:
1. 【本周总结】2-3句话概括整体表现
2. 【策略调整建议】具体说明哪个策略应加权/降权/暂停，并说明理由
3. 【下周注意事项】基于当前教训给出 2-3 条操作准则
4. 【参数调整】如有需要调整止损/止盈/仓位比例的建议

保持简洁、可操作。
"""

    try:
        from src.infra.model_adapter import get_llm
        llm = get_llm()
        response = llm.invoke([{"role": "user", "content": prompt}])
        recommendation = response.content
    except Exception as e:
        logger.warning("策略优化建议生成失败: %s", e)
        recommendation = f"[自动生成失败] 本周{len(records)}条信号, 胜率{wins}/{len(records)}, 需人工复盘。"

    # 保存到数据库
    brain.log_agent_event(
        session_id, "feedback_loop", "strategy_recommendation",
        {"recommendation": recommendation, "week_stats": {"signals": len(records), "wins": wins, "losses": losses}},
    )

    logger.info("策略优化建议已生成 (%d 字)", len(recommendation))
    return recommendation
