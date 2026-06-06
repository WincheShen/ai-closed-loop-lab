"""ChartRenderer — 为社媒发帖生成配图 (K线 / 盈亏曲线 / 板块热度)。

设计要点:
- 纯 matplotlib (Agg 后端)，不引入新依赖 (无 mplfinance)。
- 输出 PNG 到 data/charts/，返回绝对路径供 Influencer / closing_analysis 贴图。
- 已处理中文字体 (避免方块乱码) 与暗色主题。
- 任何渲染失败都返回 None，绝不阻塞主流程。

数据脱敏说明:
- 默认在标题用脱敏代码 (60xxxx)；K线本身是公开行情，不含仓位/资产信息。
- 盈亏曲线只画百分比，不画绝对金额。
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

import matplotlib

matplotlib.use("Agg")  # 无显示环境后端，必须在 pyplot 之前设置

import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.font_manager import FontProperties, findSystemFonts  # noqa: E402

logger = logging.getLogger(__name__)

# A股配色: 红涨绿跌
_UP_COLOR = "#e2483a"
_DOWN_COLOR = "#22a06b"
_BG = "#0d1117"
_PANEL = "#161b22"
_GRID = "#30363d"
_TEXT = "#c9d1d9"
_ACCENT = "#58a6ff"
_WARN = "#d29922"

# 优先尝试的中文字体 (macOS / 通用)
_CJK_FONT_CANDIDATES = [
    "PingFang SC", "Heiti SC", "Hiragino Sans GB", "STHeiti",
    "Arial Unicode MS", "Songti SC", "Microsoft YaHei", "SimHei",
    "Noto Sans CJK SC", "WenQuanYi Zen Hei",
]


def _resolve_cjk_font() -> FontProperties | None:
    """在系统已安装字体中找到第一个可用的 CJK 字体。"""
    try:
        available = {Path(p).stem for p in findSystemFonts()}
    except Exception:  # noqa: BLE001
        available = set()
    # 先按 family 名直接尝试 (matplotlib 内部可解析)
    for name in _CJK_FONT_CANDIDATES:
        try:
            fp = FontProperties(family=name)
            # 触发解析，若找不到会回退默认 (不抛错)，故用 available 双重校验
            if name.replace(" ", "") in {a.replace(" ", "") for a in available} or name in available:
                return fp
        except Exception:  # noqa: BLE001
            continue
    # macOS 常见路径兜底
    for path in ("/System/Library/Fonts/PingFang.ttc",
                 "/System/Library/Fonts/Hiragino Sans GB.ttc",
                 "/Library/Fonts/Arial Unicode.ttf"):
        if Path(path).exists():
            try:
                return FontProperties(fname=path)
            except Exception:  # noqa: BLE001
                continue
    return None


class ChartRenderer:
    """社媒配图渲染器。"""

    def __init__(self, output_dir: str | Path = "data/charts") -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._font = _resolve_cjk_font()
        if self._font is None:
            logger.warning("未找到中文字体，图表中文可能显示为方块")

    # ------------------------------------------------------------------
    # 公共渲染入口
    # ------------------------------------------------------------------

    def render_kline(
        self,
        symbol: str,
        bars: Sequence[Any],
        *,
        name: str = "",
        entry_price: float | None = None,
        target_price: float | None = None,
        stop_loss: float | None = None,
        title: str | None = None,
        mask_symbol: bool = True,
    ) -> str | None:
        """绘制日K线 + 均线 + 成交量 + 入场/目标/止损标注。

        Args:
            bars: KlineBar 列表 (含 date/open/high/low/close/volume)，或等价 dict。
            mask_symbol: True 时标题用脱敏代码 (合规)。
        """
        rows = [self._bar_to_tuple(b) for b in bars]
        rows = [r for r in rows if r is not None]
        if len(rows) < 2:
            logger.warning("K线数据不足，跳过绘图: %s", symbol)
            return None

        dates = [r[0] for r in rows]
        opens = [r[1] for r in rows]
        highs = [r[2] for r in rows]
        lows = [r[3] for r in rows]
        closes = [r[4] for r in rows]
        volumes = [r[5] for r in rows]

        disp_symbol = self._mask(symbol) if mask_symbol else symbol
        disp_name = self._mask_name(name) if (mask_symbol and name) else name
        chart_title = title or f"{disp_name} {disp_symbol} 日K".strip()

        try:
            fig, (ax, axv) = plt.subplots(
                2, 1, figsize=(9, 6), sharex=True,
                gridspec_kw={"height_ratios": [3, 1], "hspace": 0.05},
            )
            self._style_fig(fig)
            self._style_ax(ax)
            self._style_ax(axv)

            xs = mdates.date2num(dates)
            width = 0.6

            # 蜡烛
            for i in range(len(xs)):
                color = _UP_COLOR if closes[i] >= opens[i] else _DOWN_COLOR
                ax.plot([xs[i], xs[i]], [lows[i], highs[i]], color=color, linewidth=0.8, zorder=2)
                lower = min(opens[i], closes[i])
                height = abs(closes[i] - opens[i]) or (highs[i] - lows[i]) * 0.01 or 0.01
                ax.add_patch(plt.Rectangle(
                    (xs[i] - width / 2, lower), width, height,
                    facecolor=color, edgecolor=color, zorder=3,
                ))

            # 均线
            for win, col in ((5, "#f0b72f"), (10, _ACCENT), (20, "#bc8cff")):
                ma = self._moving_avg(closes, win)
                if ma:
                    ax.plot(xs[-len(ma):], ma, color=col, linewidth=1.1,
                            label=f"MA{win}", zorder=4)

            # 入场/目标/止损
            self._hline(ax, entry_price, _TEXT, "入场")
            self._hline(ax, target_price, _UP_COLOR, "目标")
            self._hline(ax, stop_loss, _DOWN_COLOR, "止损")

            ax.set_title(chart_title, color=_TEXT, fontproperties=self._font, fontsize=14, pad=10)
            leg = ax.legend(loc="upper left", framealpha=0.0, fontsize=8)
            for txt in leg.get_texts():
                txt.set_color(_TEXT)

            # 成交量
            vol_colors = [_UP_COLOR if closes[i] >= opens[i] else _DOWN_COLOR for i in range(len(xs))]
            axv.bar(xs, volumes, width=width, color=vol_colors, zorder=2)
            axv.set_ylabel("量", color=_TEXT, fontproperties=self._font, fontsize=9)

            axv.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))
            axv.xaxis.set_major_locator(mdates.AutoDateLocator())
            for label in axv.get_xticklabels():
                label.set_rotation(0)
                label.set_color(_TEXT)
                label.set_fontsize(8)

            return self._save(fig, f"kline_{symbol}")
        except Exception as e:  # noqa: BLE001
            logger.warning("K线渲染失败 %s: %s", symbol, e)
            plt.close("all")
            return None

    def render_pnl_curve(
        self,
        points: Sequence[Any],
        *,
        title: str = "近期盈亏曲线 (%)",
    ) -> str | None:
        """绘制累计盈亏百分比曲线。

        Args:
            points: [(label, pnl_pct), ...] 或 [{"label":..., "pnl_pct":...}, ...]
        """
        series = [self._point_to_tuple(p) for p in points]
        series = [s for s in series if s is not None]
        if len(series) < 2:
            logger.info("盈亏数据点不足，跳过盈亏曲线")
            return None

        labels = [s[0] for s in series]
        values = [s[1] for s in series]

        try:
            fig, ax = plt.subplots(figsize=(9, 4.5))
            self._style_fig(fig)
            self._style_ax(ax)

            xs = list(range(len(values)))
            ax.plot(xs, values, color=_ACCENT, linewidth=2, marker="o",
                    markersize=4, zorder=3)
            ax.axhline(0, color=_GRID, linewidth=1, zorder=1)
            ax.fill_between(
                xs, values, 0,
                where=[v >= 0 for v in values],
                color=_UP_COLOR, alpha=0.12, interpolate=True,
            )
            ax.fill_between(
                xs, values, 0,
                where=[v < 0 for v in values],
                color=_DOWN_COLOR, alpha=0.12, interpolate=True,
            )

            last = values[-1]
            ax.annotate(
                f"{last:+.2f}%",
                xy=(xs[-1], last),
                xytext=(8, 0), textcoords="offset points",
                color=_UP_COLOR if last >= 0 else _DOWN_COLOR,
                fontsize=12, fontweight="bold", va="center",
                fontproperties=self._font,
            )

            ax.set_xticks(xs)
            ax.set_xticklabels(labels, color=_TEXT, fontsize=8,
                               fontproperties=self._font, rotation=0)
            ax.set_title(title, color=_TEXT, fontproperties=self._font,
                         fontsize=14, pad=10)
            return self._save(fig, "pnl_curve")
        except Exception as e:  # noqa: BLE001
            logger.warning("盈亏曲线渲染失败: %s", e)
            plt.close("all")
            return None

    def render_sector_heatmap(
        self,
        sectors: Sequence[Any],
        *,
        title: str = "今日板块热度",
        top_k: int = 12,
    ) -> str | None:
        """绘制板块涨跌幅水平条形图 (按涨幅排序，红涨绿跌)。

        Args:
            sectors: [(name, change_pct), ...] 或 [{"name":..., "change_pct":...}, ...]
        """
        items = [self._sector_to_tuple(s) for s in sectors]
        items = [i for i in items if i is not None]
        if not items:
            logger.info("板块数据为空，跳过热度图")
            return None

        items.sort(key=lambda x: x[1], reverse=True)
        items = items[:top_k]
        names = [i[0] for i in items][::-1]
        changes = [i[1] for i in items][::-1]

        try:
            fig, ax = plt.subplots(figsize=(8, max(3.5, len(names) * 0.45)))
            self._style_fig(fig)
            self._style_ax(ax)

            colors = [_UP_COLOR if c >= 0 else _DOWN_COLOR for c in changes]
            ys = list(range(len(names)))
            ax.barh(ys, changes, color=colors, zorder=3, height=0.6)
            ax.axvline(0, color=_GRID, linewidth=1, zorder=1)

            for y, c in zip(ys, changes):
                ax.text(
                    c + (0.05 if c >= 0 else -0.05), y, f"{c:+.2f}%",
                    va="center", ha="left" if c >= 0 else "right",
                    color=_TEXT, fontsize=8, fontproperties=self._font,
                )

            ax.set_yticks(ys)
            ax.set_yticklabels(names, color=_TEXT, fontsize=9,
                               fontproperties=self._font)
            ax.set_title(title, color=_TEXT, fontproperties=self._font,
                         fontsize=14, pad=10)
            return self._save(fig, "sector_heatmap")
        except Exception as e:  # noqa: BLE001
            logger.warning("板块热度图渲染失败: %s", e)
            plt.close("all")
            return None

    # ------------------------------------------------------------------
    # 样式 & 工具
    # ------------------------------------------------------------------

    def _style_fig(self, fig) -> None:
        fig.patch.set_facecolor(_BG)

    def _style_ax(self, ax) -> None:
        ax.set_facecolor(_PANEL)
        ax.grid(True, color=_GRID, linewidth=0.5, alpha=0.6)
        ax.tick_params(colors=_TEXT)
        for spine in ax.spines.values():
            spine.set_color(_GRID)

    def _hline(self, ax, price: float | None, color: str, label: str) -> None:
        if not price or price <= 0:
            return
        ax.axhline(price, color=color, linewidth=1, linestyle="--", alpha=0.8, zorder=4)
        ax.text(
            0.005, price, f"{label} {price:.2f}",
            transform=ax.get_yaxis_transform(),
            color=color, fontsize=8, va="bottom",
            fontproperties=self._font,
        )

    def _save(self, fig, prefix: str) -> str:
        ts = datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]
        path = self.output_dir / f"{prefix}_{ts}.png"
        fig.tight_layout()
        fig.savefig(path, dpi=130, facecolor=_BG, bbox_inches="tight")
        plt.close(fig)
        logger.info("图表已生成: %s", path)
        return str(path.resolve())

    @staticmethod
    def _moving_avg(values: list[float], window: int) -> list[float]:
        if len(values) < window:
            return []
        out = []
        for i in range(window - 1, len(values)):
            out.append(sum(values[i - window + 1: i + 1]) / window)
        return out

    @staticmethod
    def _mask(symbol: str) -> str:
        return symbol[:2] + "xxxx" if len(symbol) >= 6 else "xxxxxx"

    @staticmethod
    def _mask_name(name: str) -> str:
        if not name:
            return ""
        if len(name) <= 2:
            return name[0] + "X"
        return f"{name[0]}X{name[-1]}"

    @staticmethod
    def _bar_to_tuple(bar: Any):
        """KlineBar 对象或 dict → (date, open, high, low, close, volume)。"""
        try:
            if isinstance(bar, dict):
                d = bar.get("date")
                o, h, l, c = bar["open"], bar["high"], bar["low"], bar["close"]
                v = bar.get("volume", 0.0)
            else:
                d = getattr(bar, "date")
                o = getattr(bar, "open")
                h = getattr(bar, "high")
                l = getattr(bar, "low")
                c = getattr(bar, "close")
                v = getattr(bar, "volume", 0.0)
            if isinstance(d, str):
                d = datetime.fromisoformat(d[:10])
            return (d, float(o), float(h), float(l), float(c), float(v))
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def _point_to_tuple(p: Any):
        try:
            if isinstance(p, dict):
                return (str(p.get("label", "")), float(p.get("pnl_pct", 0.0)))
            return (str(p[0]), float(p[1]))
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def _sector_to_tuple(s: Any):
        try:
            if isinstance(s, dict):
                return (str(s.get("name", "")), float(s.get("change_pct", 0.0)))
            if hasattr(s, "name"):
                return (str(s.name), float(getattr(s, "change_pct", 0.0)))
            return (str(s[0]), float(s[1]))
        except Exception:  # noqa: BLE001
            return None
