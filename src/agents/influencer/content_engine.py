"""Influencer Engine — 社交媒体 Agent 核心实现。

职责：
1. 抓取 Executioner 的成交记录和 Explorer 的分析图表
2. 自动生成文案："沈经理今日实录：AI选出的XX股已触达买入点..."
3. 通过小红书 CLI 发布（调用 Social-media-automation 的发布能力）
4. 收集评论区反馈，回传 Central Brain

注意：发布内容需脱敏，不暴露具体仓位和秘钥。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from src.central_brain import get_central_brain
from src.graph.state import Fill, Post, TradingState
from src.infra.config import cfg
from src.infra.logger import get_agent_logger

logger = get_agent_logger("influencer", "init")


class InfluencerEngine:
    """社交媒体内容引擎。"""

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.logger = get_agent_logger("influencer", session_id)
        self.brain = get_central_brain()

    def generate_post_from_fill(self, fill: Fill, signal: dict | None = None) -> Post:
        """根据成交记录生成发布内容。"""
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

        post: Post = {
            "post_id": f"POST-{uuid.uuid4().hex[:8].upper()}",
            "platform": "xiaohongshu",
            "title": title,
            "content": content,
            "images": [],  # TODO: 生成图表图片
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

    async def publish_post(self, post: Post, account_id: str = "XHS_02") -> Post:
        """发布内容到小红书。

        实际发布走 Social-media-automation 的 XhsCliAdapter。
        这里先留接口，后续通过 import third_party.social_media_automation 实现。
        """
        self.logger.info(
            "准备发布 — 平台=%s, 账号=%s, 标题=%s",
            post["platform"], account_id, post["title"][:30],
        )

        # TODO: 调用 third_party.social_media_automation.src.infra.xhs_cli 实际发布
        # from third_party.social_media_automation.src.infra.xhs_cli import XhsCliAdapter
        # adapter = XhsCliAdapter(account=account_id)
        # await adapter.fill_publish(post["title"], post["content"], post["images"])
        # result = await adapter.click_publish()

        # 占位：模拟发布成功
        post["url"] = f"https://www.xiaohongshu.com/explore/mock-{post['post_id']}"
        self.logger.info("发布成功 — URL=%s", post["url"])

        self.brain.log_agent_event(
            self.session_id,
            "influencer",
            "post_published",
            {"post_id": post["post_id"], "platform": post["platform"], "url": post["url"]},
        )
        return post

    async def collect_feedback(self, post: Post) -> list[dict]:
        """收集评论区反馈。"""
        # TODO: 抓取小红书评论，评估质量分数
        self.logger.info("收集评论反馈 — post_id=%s", post["post_id"])
        return []


async def run_influencer_node(state: TradingState) -> dict[str, Any]:
    """LangGraph 节点函数 — 社交媒体内容生成与发布。

    输入：含 filled_orders 的 TradingState
    输出：{"published_posts": [...], "fan_feedback": [...]}
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

    published: list[Post] = []
    all_feedback: list[dict] = []

    for fill in fills:
        # 查找关联信号
        signals = state.get("trade_signals", [])
        signal = next((s for s in signals if s["signal_id"] == fill.get("signal_id")), None)

        post = engine.generate_post_from_fill(fill, signal)
        published_post = await engine.publish_post(post)
        published.append(published_post)

        feedback = await engine.collect_feedback(published_post)
        all_feedback.extend(feedback)

    return {
        "published_posts": published,
        "fan_feedback": all_feedback,
        "logs": state.get("logs", []) + [f"[Influencer] 发布 {len(published)} 条内容"],
    }
