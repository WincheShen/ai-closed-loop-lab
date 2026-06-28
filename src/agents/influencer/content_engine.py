"""Influencer Engine — 社交媒体 Agent 核心实现。

职责：
1. 抓取 Executioner 的成交记录和 Explorer 的分析图表
2. 自动生成文案："沈经理今日实录：AI选出的XX股已触达买入点..."
3. 通过微信公众号 API 发布长文 / 小红书 SMA 发布短图文
4. 收集评论区反馈，回传 Central Brain

注意：发布内容需脱敏，不暴露具体仓位和秘钥。
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime
from typing import Any

from src.central_brain import get_central_brain
from src.graph.state import Fill, Post, TradingState
from src.infra.config import cfg
from src.infra.logger import get_agent_logger

logger = get_agent_logger("influencer", "init")


# ------------------------------------------------------------------
# 脱敏工具
# ------------------------------------------------------------------

def _mask_symbol(symbol: str) -> str:
    """6 位代码脱敏：保留前 2 位 + 'xxxx'。"""
    if len(symbol) >= 6:
        return symbol[:2] + "xxxx"
    return "xxxxxx"


def _mask_name(name: str) -> str:
    """名称脱敏：首字 + 'X' + 末字。"""
    if not name:
        return "某股"
    if len(name) <= 2:
        return name[0] + "X"
    return f"{name[0]}X{name[-1]}"


class InfluencerEngine:
    """社交媒体内容引擎。"""

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.logger = get_agent_logger("influencer", session_id)
        self.brain = get_central_brain()
        self._chart = None
        self._akshare = None

    def _gen_kline_chart(self, fill: Fill, signal: dict | None) -> str | None:
        """为成交标的生成 K线配图 (脱敏)。失败返回 None，不阻塞。"""
        try:
            if self._chart is None:
                from .chart_renderer import ChartRenderer
                from src.stock_analyzer.data_source import AkshareClient
                self._chart = ChartRenderer()
                self._akshare = AkshareClient(allow_mock_fallback=True)

            symbol = fill["symbol"]
            bars = self._akshare.fetch_kline(symbol, days=40)
            if not bars:
                return None
            return self._chart.render_kline(
                symbol,
                bars,
                name=fill.get("name", "") or (signal or {}).get("name", ""),
                entry_price=(signal or {}).get("entry_price"),
                target_price=(signal or {}).get("target_price"),
                stop_loss=(signal or {}).get("stop_loss"),
            )
        except Exception as e:  # noqa: BLE001
            self.logger.warning("K线配图生成失败 %s: %s", fill.get("symbol"), e)
            return None

    # ------------------------------------------------------------------
    # 内容生成
    # ------------------------------------------------------------------

    def generate_post_from_fill(self, fill: Fill, signal: dict | None = None) -> Post:
        """根据成交记录生成微信公众号发布内容。"""
        symbol = fill["symbol"]
        avg_price = fill["avg_price"]
        side = "买入" if fill["side"] == "buy" else "卖出"

        # 生成多个候选标题，后续可由 LLM 优化
        title_templates = [
            f"沈经理今日实录：AI选出的{symbol}已{side}，我也很紧张",
            f"AI交易日记｜{symbol} {side}点位触发，逻辑复盘",
            f"量化信号落地：{symbol} {side}均价 {avg_price:.2f}，跟吗？",
        ]
        title = title_templates[0]

        # 正文 — 脱敏处理：不暴露具体股数、不暴露总资产
        content = (
            f"📊 沈经理的AI闭环实验室今日信号落地\n\n"
            f"标的：{symbol}\n"
            f"操作：{side}\n"
            f"触发均价：{avg_price:.2f}\n\n"
            f"💡 逻辑：AI通过全市场扫描+Qlib评分+热点交叉验证筛选出该标的，"
            f"技术形态符合预设策略，今日触达买入点自动执行。\n\n"
            f"⚠️ 提示：此为AI实验记录，不构成投资建议。模拟盘先行，风险自担。\n\n"
            f"#AI交易 #量化投资 #沈经理实盘 #A股 #投资日记"
        )

        # 生成配图 (K线+标注)
        images: list[str] = []
        chart_path = self._gen_kline_chart(fill, signal)
        if chart_path:
            images.append(chart_path)

        post: Post = {
            "post_id": f"POST-{uuid.uuid4().hex[:8].upper()}",
            "platform": "wechat",
            "title": title,
            "content": content,
            "images": images,
            "url": None,
            "published_at": datetime.now().isoformat(),
            "trade_summary": {
                "symbol": symbol,
                "side": fill["side"],
                "avg_price": avg_price,
                "strategy": signal.get("strategy", "unknown") if signal else "unknown",
            },
        }
        return post

    def generate_xhs_topic_from_fills(
        self,
        fills: list[Fill],
        signals: list[dict],
        images: list[str] | None = None,
    ) -> str:
        """汇总多笔成交记录，生成小红书话题描述（脱敏版）。

        SMA 端会基于此描述进行二次创作（copywriter + strategist LLM），
        因此这里只需提供脱敏的交易事实和板块逻辑即可。
        """
        today = date.today().isoformat()
        parts = [f"今日（{today}）AI闭环实验室信号落地：\n"]

        for fill in fills:
            signal = next(
                (s for s in signals if s.get("signal_id") == fill.get("signal_id")),
                None,
            )
            side = "买入" if fill["side"] == "buy" else "卖出"
            name = fill.get("name", "") or (signal or {}).get("name", "")
            sector = (signal or {}).get("sector", "")
            strategy = (signal or {}).get("strategy", "")

            masked_name = _mask_name(name) if name else _mask_symbol(fill["symbol"])
            sector_hint = f"（{sector}方向）" if sector else ""

            parts.append(
                f"- {side} {masked_name}{sector_hint}"
                f"{'，策略: ' + strategy if strategy else ''}"
            )

        parts.append(
            "\n请基于以上已脱敏的交易记录创作小红书笔记，"
            "分享AI量化交易的思路与今日操作复盘。"
            "不出现具体股票代码与名称，重点讲板块逻辑和策略判断。"
        )
        return "\n".join(parts)

    # ------------------------------------------------------------------
    # 发布
    # ------------------------------------------------------------------

    async def publish_post(self, post: Post, account_id: str = "WX_01") -> Post:
        """发布内容到目标平台。

        默认走微信公众号；若 platform=xiaohongshu 则走 SMA dispatcher。
        """
        platform = post.get("platform", "wechat")
        self.logger.info(
            "准备发布 — 平台=%s, 账号=%s, 标题=%s",
            platform, account_id, post["title"][:30],
        )

        if platform == "wechat":
            post = await self._publish_wechat(post)
        else:
            post = await self._publish_xiaohongshu(post, account_id)

        self.brain.log_agent_event(
            self.session_id,
            "influencer",
            "post_published",
            {"post_id": post["post_id"], "platform": post["platform"], "url": post.get("url")},
        )
        return post

    async def publish_to_xhs(
        self,
        description: str,
        account_id: str,
        images: list[str] | None = None,
    ) -> Post | None:
        """通过 SMA dispatcher 发布小红书内容。

        Args:
            description: 已脱敏的话题描述（SMA 端基于此做二次创作）
            account_id: 小红书账号 ID（如 XHS_02）
            images: 配图路径列表（K线图等）

        Returns:
            Post 记录，或 None（发布失败时）
        """
        try:
            from src.social_media_dispatcher.client import SmaClient
            from src.social_media_dispatcher.schemas import TopicContext, TopicPayload

            client = SmaClient()
            context = TopicContext(images=images or [])
            payload = TopicPayload(
                account_id=account_id,
                kind="trade_record",
                description=description,
                context=context,
            )

            self.logger.info(
                "Dispatch to SMA — account=%s, kind=%s, desc_len=%d, images=%d",
                account_id, payload.kind, len(description), len(images or []),
            )
            result = client.dispatch(payload)

            if result.success:
                today = date.today().isoformat()
                sma_task_id = result.sma_task_id or ""

                self.brain.store.record_social_post(
                    sma_task_id=sma_task_id,
                    account_id=account_id,
                    platform="xiaohongshu",
                    source_pick_date=today,
                    topic=description[:100],
                )

                post: Post = {
                    "post_id": f"XHS-{sma_task_id or uuid.uuid4().hex[:8].upper()}",
                    "platform": "xiaohongshu",
                    "title": description[:40],
                    "content": description,
                    "images": images or [],
                    "url": None,  # SMA 异步发布，URL 需从 SMA 查询
                    "published_at": datetime.now().isoformat(),
                    "trade_summary": None,
                }
                self.logger.info(
                    "SMA dispatch 成功 — task_id=%s, account=%s",
                    sma_task_id, account_id,
                )
                return post
            else:
                self.logger.warning(
                    "SMA dispatch 失败 — account=%s, error=%s",
                    account_id, result.error,
                )
                return None

        except ImportError:
            self.logger.warning("SMA dispatcher 未安装，跳过小红书发布")
            return None
        except Exception as e:
            self.logger.warning("SMA dispatch 异常: %s", e)
            return None

    async def _publish_wechat(self, post: Post) -> Post:
        """通过微信公众号 API 发布。"""
        from .wechat_mp_publisher import WechatMpPublisher

        review_mode = cfg().get("social_accounts", {}).get("wechat", {}).get(
            "WX_01", {},
        ).get("review_mode", "review")
        auto = review_mode == "auto"

        content_html = self._markdown_to_html(post["content"])

        try:
            publisher = WechatMpPublisher(auto_publish=auto)
            result = publisher.publish_article(
                title=post["title"],
                content_html=content_html,
                author="沈经理AI实验室",
            )
            post["url"] = f"https://mp.weixin.qq.com/s/{result.get('draft_media_id', '')}"
            post["platform"] = "wechat"
            self.logger.info("公众号发布成功: %s", result)
        except Exception as e:
            self.logger.warning("公众号发布失败，降级保存草稿: %s", e)
            post["url"] = None

        return post

    async def _publish_xiaohongshu(self, post: Post, account_id: str) -> Post:
        """通过 SMA dispatcher 发布小红书内容。"""
        result = await self.publish_to_xhs(
            description=post["content"],
            account_id=account_id,
            images=post.get("images", []),
        )
        if result:
            post["url"] = result.get("url")
            post["platform"] = "xiaohongshu"
        else:
            post["url"] = None
        return post

    @staticmethod
    def _markdown_to_html(md_text: str) -> str:
        """简单将纯文本/markdown 转为公众号友好的 HTML。"""
        lines = md_text.split("\n")
        html_parts: list[str] = []
        for line in lines:
            stripped = line.strip()
            if not stripped:
                html_parts.append("<br/>")
            elif stripped.startswith("#"):
                text = stripped.lstrip("# ")
                html_parts.append(f"<h3>{text}</h3>")
            else:
                html_parts.append(f"<p>{stripped}</p>")
        return "\n".join(html_parts)

    async def collect_feedback(self, post: Post) -> list[dict]:
        """收集评论区反馈。"""
        # TODO: 抓取小红书评论，评估质量分数
        self.logger.info("收集评论反馈 — post_id=%s", post["post_id"])
        return []


# ------------------------------------------------------------------
# XHS 账号配置读取
# ------------------------------------------------------------------

def _get_enabled_xhs_accounts() -> list[dict]:
    """从 social_accounts 配置中获取已启用的小红书账号列表。

    Returns:
        [{"account_id": "XHS_02", "persona": "...", "track": "...", ...}, ...]
    """
    xhs_cfg = cfg().get("xiaohongshu", {})
    accounts = []
    for account_id, account_conf in xhs_cfg.items():
        if isinstance(account_conf, dict) and account_conf.get("enabled", False):
            accounts.append({"account_id": account_id, **account_conf})
    return accounts


# ------------------------------------------------------------------
# LangGraph 节点
# ------------------------------------------------------------------

async def run_influencer_node(state: TradingState) -> dict[str, Any]:
    """LangGraph 节点函数 — 社交媒体内容生成与发布。

    输入：含 filled_orders 的 TradingState
    输出：{"published_posts": [...], "fan_feedback": [...]}

    发布策略：
    - 微信公众号：每笔成交单独发一篇长文
    - 小红书：汇总所有成交生成一条脱敏话题，dispatch 给 SMA 二次创作
    """
    session_id = state["session_id"]
    engine = InfluencerEngine(session_id)

    fills = state.get("filled_orders", [])
    if not fills:
        return {
            "published_posts": [],
            "fan_feedback": [],
            "logs": state.get("logs", []) + ["[Influencer] 无成交记录，跳过"],
        }

    signals = state.get("trade_signals", [])
    published: list[Post] = []
    all_feedback: list[dict] = []

    # --- 1. 微信公众号：逐笔发布 ---
    wechat_cfg = cfg().get("wechat", {})
    wechat_enabled = any(
        v.get("enabled", False) for v in wechat_cfg.values()
        if isinstance(v, dict)
    )
    if wechat_enabled:
        for fill in fills:
            signal = next(
                (s for s in signals if s["signal_id"] == fill.get("signal_id")),
                None,
            )
            post = engine.generate_post_from_fill(fill, signal)
            published_post = await engine.publish_post(post)
            published.append(published_post)

            feedback = await engine.collect_feedback(published_post)
            all_feedback.extend(feedback)

    # --- 2. 小红书：汇总发布到 SMA ---
    xhs_accounts = _get_enabled_xhs_accounts()
    if xhs_accounts:
        # 收集所有成交的配图
        all_images: list[str] = []
        for fill in fills:
            signal = next(
                (s for s in signals if s["signal_id"] == fill.get("signal_id")),
                None,
            )
            chart_path = engine._gen_kline_chart(fill, signal)
            if chart_path:
                all_images.append(chart_path)

        # 生成脱敏话题描述
        description = engine.generate_xhs_topic_from_fills(fills, signals, all_images)

        # 为每个启用的小红书账号 dispatch
        for account in xhs_accounts:
            account_id = account["account_id"]
            xhs_post = await engine.publish_to_xhs(
                description=description,
                account_id=account_id,
                images=all_images,
            )
            if xhs_post:
                published.append(xhs_post)

    log_parts = [f"[Influencer] 发布 {len(published)} 条内容"]
    if xhs_accounts:
        log_parts.append(f"(XHS accounts: {[a['account_id'] for a in xhs_accounts]})")

    return {
        "published_posts": published,
        "fan_feedback": all_feedback,
        "logs": state.get("logs", []) + [" ".join(log_parts)],
    }
