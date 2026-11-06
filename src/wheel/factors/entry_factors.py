"""Phase 3 入场侧 Factor（screen.* / veto.* / detector.* / rule.* / mod.*）。

涵盖：
- 3.1 隔夜客尾盘狙击：screen.cold_and_weather_gate / screen.watchlist_ttl /
      veto.cold_period / detector.ultra_tail_momentum
- 3.2 次日开盘成交：veto.quote_fresh_today / veto.open_gap_guard / rule.fill_at_open
- 3.3 条件单触发：veto.signal_price_anomaly / veto.stock_cooldown_and_suspend /
      rule.pending_condition_met / mod.portfolio_position_cap
"""
from __future__ import annotations

import logging

from src.agents.explorer.short_term_watchlist import MAX_WATCH_DAYS
from src.agents.reviewer.tail_hunt import check_cold_period
from src.stock_analyzer.detectors.ultra_short_momentum import (
    STRATEGY_ID as ULTRA_STRATEGY_ID,
    _CONT_CLOSE_POS,
    _CONT_RET5,
    _CONT_RET20,
    _CONT_VOL_FLOOR,
    _MIN_AMPLITUDE,
    _MIN_TURNOVER,
    _PB_DD_MAX,
    _PB_DD_MIN,
    _PB_RET20,
)
from src.wheel.protocol import Context, Opinion

logger = logging.getLogger(__name__)


class ColdAndWeatherScreenFactor:
    """screen.cold_and_weather_gate — 隔夜客冷静期、恶劣天气与账户容量前置筛查。"""

    name = "screen.cold_and_weather_gate"
    stage = "screen"
    requires = ("candidates",)
    param_keys = ("max_open_positions", "min_cash")

    def evaluate(self, ctx: Context, params: dict) -> Opinion:
        candidates = list(ctx.data.get("candidates") or [])
        persona_id = ctx.persona.persona_id or "ultra_short_momentum_v1"
        if ctx.brain is not None:
            is_cold, reason = check_cold_period(
                ctx.brain, persona_id=persona_id, as_of=ctx.now,
            )
            if is_cold:
                return Opinion(
                    factor=self.name,
                    verdict="pass",
                    reason=reason,
                    evidence={"kept": [], "eliminated": len(candidates), "gate": "cold_or_weather"},
                )
            max_open = int(params.get("max_open_positions", 2))
            open_pos = ctx.brain.store.list_open_positions(persona_id=persona_id)
            if len(open_pos) >= max_open:
                return Opinion(
                    factor=self.name,
                    verdict="pass",
                    reason=f"当前已有 {len(open_pos)} 只持仓 (上限 {max_open} 只)，跳过开新仓",
                    evidence={"kept": [], "eliminated": len(candidates), "gate": "max_open_positions"},
                )
            min_cash = float(params.get("min_cash", 10000.0))
            account = ctx.brain.store.get_account_by_persona(persona_id)
            if account and float(account.get("available_cash", 300000.0)) < min_cash:
                return Opinion(
                    factor=self.name,
                    verdict="pass",
                    reason=f"可用资金不足 (< {min_cash:.0f})",
                    evidence={"kept": [], "eliminated": len(candidates), "gate": "insufficient_cash"},
                )
        return Opinion(
            factor=self.name,
            verdict="pass",
            reason=f"前置闸门通过，保留 {len(candidates)} 只候选",
            evidence={"kept": candidates},
        )


class WatchlistTtlScreenFactor:
    """screen.watchlist_ttl — 剔除超过观察期上限（默认 5 天）未触发的自选股。"""

    name = "screen.watchlist_ttl"
    stage = "screen"
    requires = ("candidates",)
    param_keys = ("max_watch_days",)

    def evaluate(self, ctx: Context, params: dict) -> Opinion:
        candidates = list(ctx.data.get("candidates") or [])
        max_days = int(params.get("max_watch_days", MAX_WATCH_DAYS))
        kept: list[dict] = []
        expired: list[str] = []
        for item in candidates:
            days_watched = int(item.get("days_watched", 0) or 0)
            if days_watched >= max_days:
                expired.append(str(item.get("symbol") or ""))
                if ctx.brain is not None and ctx.mode == "live" and item.get("watch_id"):
                    try:
                        ctx.brain.store.remove_from_watchlist(
                            item["watch_id"],
                            reason=f"入池 {days_watched} 天超过 {max_days} 天未触发入场，时间止损踢出",
                        )
                    except Exception:  # noqa: BLE001
                        pass
                continue
            kept.append(item)
        return Opinion(
            factor=self.name,
            verdict="pass",
            reason=f"TTL 筛选保留 {len(kept)} 只，超期剔除 {len(expired)} 只",
            evidence={"kept": kept, "expired": expired},
        )


class ColdPeriodVetoFactor:
    """veto.cold_period — 单票入场前的冷静期与大盘恶劣天气否决。"""

    name = "veto.cold_period"
    stage = "veto"
    requires: tuple[str, ...] = ()
    param_keys: tuple[str, ...] = ()

    def evaluate(self, ctx: Context, params: dict) -> Opinion:
        if ctx.brain is None:
            return Opinion(factor=self.name, verdict="pass")
        persona_id = ctx.persona.persona_id or "ultra_short_momentum_v1"
        is_cold, reason = check_cold_period(
            ctx.brain, persona_id=persona_id, as_of=ctx.now,
        )
        if is_cold:
            return Opinion(factor=self.name, verdict="veto", reason=reason)
        return Opinion(factor=self.name, verdict="pass")


class UltraTailMomentumDetector:
    """detector.ultra_tail_momentum — 隔夜客 14:35 尾盘动量延续 / 强势企稳检测器。"""

    name = "detector.ultra_tail_momentum"
    stage = "propose"
    requires = ("candidate", "quote", "intraday_metrics")
    param_keys = ("min_turnover", "min_amplitude")

    def evaluate(self, ctx: Context, params: dict) -> Opinion:
        cand = ctx.data.get("candidate") or {}
        metrics = ctx.data.get("intraday_metrics") or {}
        if not metrics.get("valid"):
            return Opinion(
                factor=self.name,
                verdict="pass",
                reason=str(metrics.get("reason") or "盘中指标无效"),
            )

        symbol = str(cand.get("symbol") or (ctx.event.subject or {}).get("symbol") or "")
        name = str(cand.get("name") or symbol)
        current_price = float(metrics.get("price") or 0.0)
        open_p = float(metrics.get("open") or 0.0)
        low = float(metrics.get("low") or current_price)
        day_range = float(metrics.get("day_range") or 0.0)
        close_pos = float(metrics.get("close_pos") or 0.5)
        amplitude = float(metrics.get("amplitude") or 0.0)
        turnover = float(metrics.get("turnover") or 0.0)
        change_pct = float(metrics.get("change_pct") or 0.0)
        ret5 = metrics.get("ret5")
        ret20 = metrics.get("ret20")
        drawdown = metrics.get("drawdown")
        avg_to_20 = float(metrics.get("avg_to_20") or 0.0)

        min_turnover = float(params.get("min_turnover", _MIN_TURNOVER))
        min_amplitude = float(params.get("min_amplitude", _MIN_AMPLITUDE))
        if turnover < min_turnover or amplitude < min_amplitude:
            return Opinion(
                factor=self.name,
                verdict="pass",
                reason=f"流动性/振幅不足 (turnover={turnover/1e8:.2f}亿, amplitude={amplitude:.1%})",
            )

        has_hist = ret5 is not None and ret20 is not None and drawdown is not None
        matched_setup: str | None = None

        if (
            change_pct >= 0.0
            and current_price >= open_p
            and close_pos >= _CONT_CLOSE_POS
            and (
                not has_hist
                or (
                    float(ret5) >= _CONT_RET5
                    and float(ret20) >= _CONT_RET20
                    and (avg_to_20 <= 0 or turnover >= avg_to_20 * _CONT_VOL_FLOOR)
                )
            )
        ):
            matched_setup = "momentum_continuation"
        elif (
            change_pct >= -7.0
            and (
                current_price >= open_p
                or (min(open_p, current_price) - low) / max(day_range, 0.01) >= 0.4
            )
            and (
                not has_hist
                or (
                    float(ret20) >= _PB_RET20
                    and _PB_DD_MIN <= float(drawdown) <= _PB_DD_MAX
                )
            )
        ):
            matched_setup = "strong_pullback"

        if not matched_setup:
            if has_hist and (
                float(ret20) < _CONT_RET20
                or (float(ret5) < _CONT_RET5 and float(ret20) < _PB_RET20)
            ):
                reason = (
                    f"多日动能不足 (ret5={float(ret5):.1%}, ret20={float(ret20):.1%}, "
                    f"change_pct={change_pct:+.2f}%)"
                )
            else:
                reason = "未匹配尾盘形态 (非强势延续/强势企稳)"
            return Opinion(factor=self.name, verdict="pass", reason=reason)

        setup_bonus = 10.0 if matched_setup == "momentum_continuation" else 0.0
        mom_bonus = (
            min(max(float(ret5 or 0.0) * 100.0, 0.0), 30.0) * 0.8
            + min(max(float(ret20 or 0.0) * 100.0, 0.0), 50.0) * 0.4
        )
        score = round(
            setup_bonus
            + (close_pos * 25.0)
            + (min(turnover / 1e8, 20.0) * 1.0)
            + (change_pct * 4.0)
            + mom_bonus,
            2,
        )
        rationale = (
            f"14:35尾盘狙击触发: 形态={matched_setup}, 现价={current_price:.2f}, "
            f"振幅={amplitude:.1%}, 成交额={turnover/1e8:.2f}亿, score={score:.2f}"
        )
        return Opinion(
            factor=self.name,
            verdict="propose",
            action="BUY",
            qty_pct=0.20,
            priority=100,
            reason=rationale,
            evidence={
                "symbol": symbol,
                "name": name,
                "price": current_price,
                "score": score,
                "setup": matched_setup,
                "turnover": turnover,
                "amplitude": amplitude,
                "change_pct": change_pct,
                "stop_loss": round(current_price * 0.975, 2),
                "target_price": round(current_price * 1.08, 2),
                "strategy": ULTRA_STRATEGY_ID,
                "strategy_id": ULTRA_STRATEGY_ID,
                "watch_id": cand.get("watch_id"),
            },
        )


# ----------------------------------------------------------------------
# Phase 3.2: order_fill_at_open（次日开盘成交 + 跳空保护）
# ----------------------------------------------------------------------

class QuoteFreshTodayVetoFactor:
    """veto.quote_fresh_today — 校验开盘行情必须存在且 trade_date == today（防 09:30 旧数据）。"""

    name = "veto.quote_fresh_today"
    stage = "veto"
    requires = ("quote",)
    param_keys: tuple[str, ...] = ()

    def evaluate(self, ctx: Context, params: dict) -> Opinion:
        quote = ctx.data.get("quote") or {}
        price = float(quote.get("price") or 0.0)
        if quote.get("is_mock") or price <= 0:
            return Opinion(factor=self.name, verdict="veto", reason="缺少真实开盘行情")
        today_iso = ctx.now.date().isoformat()
        trade_date = quote.get("trade_date")
        snap = quote.get("snapshot")
        if not trade_date and snap is not None and getattr(snap, "bars", None):
            trade_date = snap.bars[-1].timestamp.date().isoformat()
        if trade_date and str(trade_date)[:10] != today_iso:
            return Opinion(
                factor=self.name,
                verdict="veto",
                reason=f"行情日期 {str(trade_date)[:10]} 非今日 {today_iso}（疑似昨日残留收盘价）",
            )
        return Opinion(factor=self.name, verdict="pass")


class OpenGapGuardVetoFactor:
    """veto.open_gap_guard — 次日开盘大幅高开/低开破止损/涨跌停保护（Phase 3.2）。"""

    name = "veto.open_gap_guard"
    stage = "veto"
    requires = ("signal", "quote")
    param_keys = ("max_gap_up_pct",)

    def evaluate(self, ctx: Context, params: dict) -> Opinion:
        sig = ctx.data.get("signal") or {}
        quote = ctx.data.get("quote") or {}
        price = float(quote.get("price") or 0.0)
        if price <= 0:
            return Opinion(factor=self.name, verdict="veto", reason="无有效开盘价")

        ref_price = float(sig.get("entry_price") or sig.get("current_price") or 0.0)
        stop_loss = float(sig.get("stop_loss") or 0.0)
        max_gap_up_pct = float(params.get("max_gap_up_pct", 3.0))

        # 1. 大幅低开直接跌破止损价 → 放弃成交
        if stop_loss > 0 and price <= stop_loss:
            return Opinion(
                factor=self.name,
                verdict="veto",
                reason=f"开盘价 {price:.2f} 已跌破信号止损价 {stop_loss:.2f}，放弃成交",
            )

        # 2. 大幅高开超基准价 +max_gap_up_pct% → 盈亏比恶化放弃成交
        if ref_price > 0:
            gap_pct = (price / ref_price - 1.0) * 100.0
            if gap_pct > max_gap_up_pct:
                return Opinion(
                    factor=self.name,
                    verdict="veto",
                    reason=(
                        f"开盘价 {price:.2f} 高于信号基准价 {ref_price:.2f} 达 "
                        f"+{gap_pct:.1f}% (上限 +{max_gap_up_pct:.1f}%)，盈亏比恶化放弃成交"
                    ),
                )

        # 3. 开盘涨跌停封死保护
        change_pct = quote.get("change_pct")
        snap = quote.get("snapshot")
        if change_pct is None and snap is not None:
            change_pct = getattr(snap, "change_pct", None)
        if change_pct is not None:
            symbol = str(sig.get("symbol") or quote.get("symbol") or "")
            limit_pct = 19.8 if symbol.startswith(("30", "68")) else 9.8
            if abs(float(change_pct)) >= limit_pct:
                return Opinion(
                    factor=self.name,
                    verdict="veto",
                    reason=f"开盘涨跌停封死 ({float(change_pct):+.2f}%)，无法正常成交",
                )

        return Opinion(factor=self.name, verdict="pass")


class FillAtOpenRuleFactor:
    """rule.fill_at_open — 按校验通过的次日开盘价提议 BUY 成交（Phase 3.2）。"""

    name = "rule.fill_at_open"
    stage = "propose"
    requires = ("signal", "quote")
    param_keys: tuple[str, ...] = ()

    def evaluate(self, ctx: Context, params: dict) -> Opinion:
        sig = ctx.data.get("signal") or {}
        quote = ctx.data.get("quote") or {}
        price = float(quote.get("price") or 0.0)
        if price <= 0:
            return Opinion(factor=self.name, verdict="pass", reason="无开盘价格")
        symbol = str(sig.get("symbol") or quote.get("symbol") or "")
        qty_pct = float(sig.get("approved_position_pct") or sig.get("position_pct") or 0.05)
        return Opinion(
            factor=self.name,
            verdict="propose",
            action="BUY",
            qty_pct=qty_pct,
            priority=100,
            reason=f"次日开盘价成交 @ {price:.2f} (原信号 {sig.get('signal_id', '')})",
            evidence={
                "signal_id": sig.get("signal_id"),
                "symbol": symbol,
                "name": sig.get("name", symbol),
                "sector": sig.get("sector", ""),
                "price": price,
                "stop_loss": sig.get("stop_loss"),
                "target_price": sig.get("target_price"),
                "position_pct": qty_pct,
                "strategy": sig.get("strategy", ""),
                "strategy_id": sig.get("strategy_id", ""),
            },
        )


# ----------------------------------------------------------------------
# Phase 3.3: pending_trigger（盘中条件单触发转盘）
# ----------------------------------------------------------------------

class SignalPriceAnomalyVetoFactor:
    """veto.signal_price_anomaly — 现价偏离条件单入场价 > 30% 视为异常数据否决。"""

    name = "veto.signal_price_anomaly"
    stage = "veto"
    requires = ("signal", "quote")
    param_keys = ("max_deviation_pct",)

    def evaluate(self, ctx: Context, params: dict) -> Opinion:
        sig = ctx.data.get("signal") or {}
        quote = ctx.data.get("quote") or {}
        price = float(quote.get("price") or 0.0)
        entry_price = float(sig.get("entry_price") or 0.0)
        max_dev = float(params.get("max_deviation_pct", 30.0)) / 100.0
        if entry_price > 0 and price > 0:
            dev = abs(price - entry_price) / entry_price
            if dev > max_dev:
                return Opinion(
                    factor=self.name,
                    verdict="veto",
                    reason=f"当前价 {price:.2f} 偏离入场价 {entry_price:.2f} 达 {dev:.0%}，疑似数据异常",
                )
        return Opinion(factor=self.name, verdict="pass")


class StockCooldownAndSuspendVetoFactor:
    """veto.stock_cooldown_and_suspend — 经验层个股冷却期与策略 SUSPEND 否决。"""

    name = "veto.stock_cooldown_and_suspend"
    stage = "veto"
    requires = ("signal",)
    param_keys: tuple[str, ...] = ()

    def evaluate(self, ctx: Context, params: dict) -> Opinion:
        sig = ctx.data.get("signal") or {}
        symbol = str(sig.get("symbol") or "")
        strategy_id = str(sig.get("strategy_id") or "")
        persona_id = str(sig.get("persona_id") or ctx.persona.persona_id or "") or None
        try:
            if ctx.brain is not None:
                from src.experience_layer.experience_api import ExperienceAPI
                exp = ExperienceAPI(brain=ctx.brain)
            else:
                from src.experience_layer import get_experience
                exp = get_experience()
            if symbol and exp.is_stock_in_cooldown(
                symbol, persona_id=persona_id, as_of=ctx.now,
            ):
                return Opinion(factor=self.name, verdict="veto", reason=f"{symbol} 处于个股冷却期")
            if strategy_id and ctx.brain is not None:
                # 回放时钟：只读 ctx.now 当日及之前的 regime 快照
                snap = ctx.brain.store.latest_market_regime(
                    as_of=ctx.now.date().isoformat(),
                )
                cur_regime = (snap.get("regime") if snap else "") or ""
                if cur_regime:
                    stats = exp.get_strategy_stats(
                        strategy_id, regime=cur_regime, persona_id=persona_id,
                        as_of=ctx.now,
                    )
                    if stats and stats.get("recommendation") == "SUSPEND":
                        return Opinion(
                            factor=self.name,
                            verdict="veto",
                            reason=f"策略 {strategy_id} 在 {cur_regime} 环境下已暂停",
                        )
        except Exception as e:  # noqa: BLE001
            return Opinion(factor=self.name, verdict="veto", reason=f"经验层重评估异常: {e}")
        return Opinion(factor=self.name, verdict="pass")


class PendingConditionMetRuleFactor:
    """rule.pending_condition_met — 检查 breakout / pullback 价格条件是否达成。"""

    name = "rule.pending_condition_met"
    stage = "propose"
    requires = ("signal", "quote")
    param_keys: tuple[str, ...] = ()

    def evaluate(self, ctx: Context, params: dict) -> Opinion:
        sig = ctx.data.get("signal") or {}
        quote = ctx.data.get("quote") or {}
        price = float(quote.get("price") or 0.0)
        entry_price = float(sig.get("entry_price") or 0.0)
        condition = str(sig.get("entry_condition") or "breakout")
        if price <= 0 or entry_price <= 0:
            return Opinion(factor=self.name, verdict="pass", reason="价格无效")

        triggered = (
            (condition == "breakout" and price >= entry_price)
            or (condition == "pullback" and price <= entry_price)
        )
        if not triggered:
            return Opinion(
                factor=self.name,
                verdict="pass",
                reason=f"未触发 ({condition}: 当前 {price:.2f} vs 入场 {entry_price:.2f})",
            )

        symbol = str(sig.get("symbol") or quote.get("symbol") or "")
        qty_pct = float(sig.get("approved_position_pct") or sig.get("position_pct") or 0.05)
        return Opinion(
            factor=self.name,
            verdict="propose",
            action="BUY",
            qty_pct=qty_pct,
            priority=100,
            reason=f"条件单触发({condition}): 当前价 {price:.2f} 达到入场价 {entry_price:.2f}",
            evidence={
                "signal_id": sig.get("signal_id"),
                "symbol": symbol,
                "name": sig.get("name", symbol),
                "sector": sig.get("sector", ""),
                "price": price,
                "stop_loss": sig.get("stop_loss"),
                "target_price": sig.get("target_price"),
                "position_pct": qty_pct,
                "strategy": sig.get("strategy", ""),
                "strategy_id": sig.get("strategy_id", ""),
            },
        )


class PortfolioPositionCapModifier:
    """mod.portfolio_position_cap — 组合总仓位已达上限时将 BUY 调弱为 HOLD。

    signal 只是 persona_id 的可选回退来源，不是硬依赖（entry_confirm 的
    数据步产出 candidate/strategist_signal 而非 signal，requires 若声明
    signal 会导致本修饰器永远被依赖检查 skip）。"""

    name = "mod.portfolio_position_cap"
    stage = "modify"
    requires: tuple[str, ...] = ()
    param_keys: tuple[str, ...] = ()

    def evaluate(self, ctx: Context, params: dict) -> Opinion:
        resolved = ctx.resolved
        if not resolved or resolved.action not in ("BUY", "ADD") or ctx.brain is None:
            return Opinion(factor=self.name, verdict="pass")
        sig = ctx.data.get("signal") or ctx.data.get("strategist_signal") or {}
        persona_id = str(sig.get("persona_id") or ctx.persona.persona_id or "")
        if not persona_id:
            return Opinion(factor=self.name, verdict="pass")
        try:
            from src.agents.cio.trading_persona import get_persona
            from src.infra.config import cfg as app_cfg
            persona_obj = get_persona(persona_id=persona_id)
            # 直接按 EntryGateway 口径计算，不构造 RiskGovernor（其 __init__
            # 会触达全局 get_central_brain，回放/注入库下会串库）
            positions = ctx.brain.store.list_open_positions(persona_id=persona_id)
            if getattr(ctx, "mode", "shadow") != "live":
                # 影子评估同一 signal 的旧路径持仓不计入已用仓位，
                # 否则 order_fill_at_open 场景被算成"旧持仓+新提议"双倍仓位
                ev_sig = str(
                    (getattr(resolved, "evidence", None) or {}).get("signal_id")
                    or sig.get("signal_id") or ""
                )
                if ev_sig:
                    positions = [
                        p for p in positions
                        if str(p.get("original_signal_id") or p.get("signal_id") or "")
                        != ev_sig
                    ]
            capital = float(
                getattr(persona_obj, "capital", 0.0)
                or app_cfg().get("initial_capital", 300000)
            )
            used_cost = sum(
                float(p.get("entry_price") or 0) * int(p.get("current_qty") or 0)
                for p in positions
            )
            used_pct = used_cost / capital if capital > 0 else 0.0
            # regime 快照优先取本链路数据步注入的，其次库内最新
            regime_snap = dict(
                ctx.data.get("regime_snapshot")
                or ctx.brain.store.latest_market_regime()
                or {}
            )
            regime = str(regime_snap.get("regime") or "neutral")
            max_total_pct = float(
                regime_snap.get("max_total_position_pct")
                or persona_obj.max_total_position_for(regime)
            )
            if used_pct >= max_total_pct:
                return Opinion(
                    factor=self.name,
                    verdict="modify",
                    action="HOLD",
                    reason=f"组合仓位已达上限 ({used_pct:.0%} >= {max_total_pct:.0%})",
                )
        except Exception as e:  # noqa: BLE001
            logger.debug("mod.portfolio_position_cap 检查异常: %s", e)
        return Opinion(factor=self.name, verdict="pass")


# ----------------------------------------------------------------------
# Phase 4: 账户级风险事件与熔断 Factor
# ----------------------------------------------------------------------

class AccountDailyLossVetoFactor:
    """veto.account_daily_loss — 当日已实现亏损超角色上限（max_daily_loss_pct）时禁止新开仓（Phase 4.2）。"""

    name = "veto.account_daily_loss"
    stage = "veto"
    requires = ("account_risk",)
    param_keys: tuple[str, ...] = ("max_daily_loss_pct",)

    def evaluate(self, ctx: Context, params: dict) -> Opinion:
        risk = ctx.data.get("account_risk") or {}
        if risk.get("daily_loss_breached"):
            return Opinion(
                factor=self.name,
                verdict="veto",
                reason=(
                    f"当日已实现亏损 {float(risk.get('daily_pnl') or 0):.2f} 超过限制 "
                    f"{float(risk.get('daily_limit') or 0):.2f} ({float(risk.get('max_daily_loss_pct') or 0):.1%})，禁止新开仓"
                ),
            )
        return Opinion(factor=self.name, verdict="pass")


class ConsecutiveLossThrottleModifier:
    """mod.consecutive_loss_throttle — 连续亏损达角色上限（consecutive_loss_limit）时拦截买入（Phase 4.3）。"""

    name = "mod.consecutive_loss_throttle"
    stage = "modify"
    requires = ("account_risk",)
    param_keys: tuple[str, ...] = ("consecutive_loss_limit",)

    def evaluate(self, ctx: Context, params: dict) -> Opinion:
        resolved = ctx.resolved
        if not resolved or resolved.action not in ("BUY", "ADD"):
            return Opinion(factor=self.name, verdict="pass")
        risk = ctx.data.get("account_risk") or {}
        limit = int(params.get("consecutive_loss_limit", risk.get("consecutive_loss_limit", 5)))
        losses = int(risk.get("consecutive_losses", 0))
        if losses < limit:
            return Opinion(factor=self.name, verdict="pass")
        if "consecutive_loss_breached" in risk and limit == int(risk.get("consecutive_loss_limit", 5)):
            active = bool(risk["consecutive_loss_breached"])
        elif risk.get("days_since_last_loss") is not None:
            cooldown = int(params.get("consecutive_loss_cooldown_days", risk.get("cooldown_days", 1)))
            active = int(risk["days_since_last_loss"]) <= cooldown
        else:
            active = bool(risk.get("streak_active", True))
        if not active:
            return Opinion(factor=self.name, verdict="pass")
        return Opinion(
            factor=self.name,
            verdict="modify",
            action="HOLD",
            reason=f"最近 {risk.get('window_days', 14)} 天内连续亏损 {losses} 笔 (限制 {limit})，连亏冷静期内停止买入",
        )


# ----------------------------------------------------------------------
# Phase 3.4 & 3.8: 日度选股漏斗 / LLM 信号提议 / 记忆类转盘 Factor
# ----------------------------------------------------------------------

class RegimePostureScreenFactor:
    """screen.regime_posture_gate — data_quality 异常或 posture=exit 时清空候选池（Phase 3.4）。"""

    name = "screen.regime_posture_gate"
    stage = "screen"
    requires = ("candidates",)
    param_keys: tuple[str, ...] = ()

    def evaluate(self, ctx: Context, params: dict) -> Opinion:
        from src.infra.data_quality import blocks_new_entries

        candidates = list(ctx.data.get("candidates") or [])
        regime = ctx.data.get("regime_snapshot") or {}
        if blocks_new_entries(regime):
            return Opinion(
                factor=self.name,
                verdict="pass",
                reason=f"data_quality={regime.get('data_quality')} 禁止新开仓",
                evidence={"kept": [], "eliminated": len(candidates)},
            )
        posture = str(regime.get("recommended_posture") or "")
        regime_label = str(regime.get("regime") or "")
        # 与 RiskGovernor 对齐: bear/panic 下进攻姿态强制降级为 defend/exit
        effective_posture = posture
        if regime_label in ("bear", "panic") and posture in ("attack", "selective_attack"):
            effective_posture = "defend" if regime_label == "bear" else "exit"
        if effective_posture in ("exit", "defend", "observe"):
            label = {"exit": "撤退", "defend": "防守", "observe": "观望"}[effective_posture]
            return Opinion(
                factor=self.name,
                verdict="pass",
                reason=(
                    f"MarketBrain posture={effective_posture} ({label}, regime={regime_label})"
                    "，禁止新开仓"
                ),
                evidence={"kept": [], "eliminated": len(candidates)},
            )
        return Opinion(
            factor=self.name,
            verdict="pass",
            reason=f"市场环境允许选股，保留 {len(candidates)} 只候选",
            evidence={"kept": candidates},
        )


class SharedFlagsAndBlacklistScreenFactor:
    """screen.shared_flags_and_blacklist — 过滤跨角色风险标记（shared_stock_flags）与角色黑名单（Phase 3.7）。"""

    name = "screen.shared_flags_and_blacklist"
    stage = "screen"
    requires = ("candidates",)
    param_keys: tuple[str, ...] = ()

    def evaluate(self, ctx: Context, params: dict) -> Opinion:
        candidates = list(ctx.data.get("candidates") or [])
        kept: list[dict] = []
        blocked: list[str] = []
        blacklist_syms: set[str] = set()
        if ctx.brain is not None:
            try:
                from src.experience_layer.dynamic_blacklist import DynamicBlacklist
                bl = DynamicBlacklist(
                    persona_id=ctx.persona.persona_id or None, brain=ctx.brain,
                )
                blacklist_syms = set(bl.get_blacklisted_symbols(as_of=ctx.now))
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "screen.shared_flags_and_blacklist 黑名单加载失败（放行全部候选）: %s", e,
                )

        for item in candidates:
            sym = str(item.get("symbol") or "")
            if not sym:
                continue
            if sym in blacklist_syms:
                blocked.append(f"{sym}:blacklist")
                continue
            if ctx.brain is not None:
                try:
                    flags = ctx.brain.store.get_shared_stock_flags(sym)
                    # 回放时钟：ctx.now 之后才打上的风险标记属于未来数据
                    flags = [
                        f for f in (flags or [])
                        if (f.get("created_at") or "") <= ctx.now.isoformat()
                    ]
                    if flags:
                        blocked.append(f"{sym}:{flags[0].get('flag_type', 'flagged')}")
                        continue
                except Exception:  # noqa: BLE001
                    pass
            kept.append(item)

        return Opinion(
            factor=self.name,
            verdict="pass",
            reason=f"黑名单与共享风险过滤保留 {len(kept)} 只，拦截 {len(blocked)} 只",
            evidence={"kept": kept, "blocked": blocked},
        )


class LlmSignalProposeFactor:
    """llm.signal_propose — 将 Strategist 产出的 BUY 信号映射为 priority=500 的入场提议（Phase 3.4）。"""

    name = "llm.signal_propose"
    stage = "propose"
    requires = ("candidate", "quote")
    param_keys: tuple[str, ...] = ()

    def evaluate(self, ctx: Context, params: dict) -> Opinion:
        sig = ctx.data.get("strategist_signal")
        if not sig or str(sig.get("action") or "").upper() != "BUY":
            return Opinion(factor=self.name, verdict="pass", reason="Strategist 未产出买入信号")
        cand = ctx.data.get("candidate") or {}
        quote = ctx.data.get("quote") or {}
        symbol = str(sig.get("symbol") or cand.get("symbol") or "")
        price = float(sig.get("entry_price") or quote.get("price") or 0.0)
        # 只表达 Strategist 原始提议仓位；approved_position_pct 是旧链路 RiskGovernor
        # 的裁决结果（拒单时为 0.0），组合级截断由 mod.portfolio_position_cap 与
        # EntryGateway 统一负责
        qty_pct = float(sig.get("position_pct") or 0.05)
        score = float(sig.get("confidence") or cand.get("qlib_score") or 0.5) * 100.0
        return Opinion(
            factor=self.name,
            verdict="propose",
            action="BUY",
            qty_pct=qty_pct,
            priority=500,
            reason=str(sig.get("rationale") or "Strategist 提议买入"),
            evidence={
                "signal_id": sig.get("signal_id"),
                "symbol": symbol,
                "name": sig.get("name") or cand.get("name") or symbol,
                "sector": sig.get("sector") or cand.get("sector") or "",
                "price": price,
                "score": round(score, 2),
                "stop_loss": sig.get("stop_loss"),
                "target_price": sig.get("target_price"),
                "position_pct": qty_pct,
                "strategy": sig.get("strategy", ""),
                "strategy_id": sig.get("strategy_id", ""),
                "entry_condition": sig.get("entry_condition", "immediate"),
            },
        )


class MemoryUpdateRuleFactor:
    """rule.memory_update — 记忆类转盘通用记录步骤（Phase 3.8 / 4b，产出 WATCH 记录轨迹）。"""

    name = "rule.memory_update"
    stage = "propose"
    requires: tuple[str, ...] = ()
    param_keys: tuple[str, ...] = ("task",)

    def evaluate(self, ctx: Context, params: dict) -> Opinion:
        task = str(params.get("task") or ctx.event.type or "memory_update")
        return Opinion(
            factor=self.name,
            verdict="propose",
            action="WATCH",
            priority=100,
            reason=f"记忆类转盘执行: {task}",
            evidence={"task": task, "payload": dict(ctx.event.payload or {})},
        )


def register(reg) -> None:
    reg.register_factor(ColdAndWeatherScreenFactor())
    reg.register_factor(WatchlistTtlScreenFactor())
    reg.register_factor(ColdPeriodVetoFactor())
    reg.register_factor(UltraTailMomentumDetector())
    reg.register_factor(QuoteFreshTodayVetoFactor())
    reg.register_factor(OpenGapGuardVetoFactor())
    reg.register_factor(FillAtOpenRuleFactor())
    reg.register_factor(SignalPriceAnomalyVetoFactor())
    reg.register_factor(StockCooldownAndSuspendVetoFactor())
    reg.register_factor(PendingConditionMetRuleFactor())
    reg.register_factor(PortfolioPositionCapModifier())
    reg.register_factor(AccountDailyLossVetoFactor())
    reg.register_factor(ConsecutiveLossThrottleModifier())
    reg.register_factor(RegimePostureScreenFactor())
    reg.register_factor(SharedFlagsAndBlacklistScreenFactor())
    reg.register_factor(LlmSignalProposeFactor())
    reg.register_factor(MemoryUpdateRuleFactor())
