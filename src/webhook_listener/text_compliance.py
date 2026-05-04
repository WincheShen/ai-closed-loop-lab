"""文字合规改写。

防封号要点（FR-2.3）：
- 替换敏感词："买入"→"上车"、"建仓"→"关注"等
- 价格区间化："19.8" → "20元附近"
- 去除违规词："必涨"、"老师推荐"、"内幕" → 直接删除或拒绝发布

注意：本模块只做规则替换，最终发文还需 LLM 复审。
"""
from __future__ import annotations

import re
from dataclasses import dataclass


# (pattern, replacement)，pattern 支持正则
_REPLACEMENTS: list[tuple[str, str]] = [
    (r"买入", "上车"),
    (r"卖出", "下车"),
    (r"建仓", "关注"),
    (r"加仓", "继续关注"),
    (r"减仓", "兑现一些"),
    (r"清仓", "暂时离场"),
    (r"满仓", "重点关注"),
    (r"重仓", "看好"),
    (r"抄底", "底部布局"),
    (r"逃顶", "高位减压"),
    (r"涨停", "强势"),
    (r"跌停", "回调较深"),
]

# 违规词：出现即拒绝（让上层判断不发布）
_FORBIDDEN: list[str] = [
    "必涨", "稳赚", "保赚", "翻倍", "黑马", "牛股",
    "老师推荐", "内幕", "庄家", "主力消息", "操盘手",
    "100%", "包赚", "无风险", "稳定盈利",
]

# 价格区间化：匹配类似 19.8 / 19.85 / 19.85元 / 12.3-12.5
_PRICE_RE = re.compile(r"(?<!\d)(\d{1,4}\.\d{1,2})(?!\d)")


@dataclass
class ComplianceResult:
    original: str
    safe_text: str
    forbidden_hits: list[str]      # 命中的违规词
    is_publishable: bool            # 是否可继续走发布流程
    replacements_applied: int


def round_price_to_zone(price: float) -> str:
    """把精确价格变成区间表达。

    19.85 → 20元附近
    8.32  → 8元附近
    127.5 → 130元附近
    """
    if price < 10:
        return f"{round(price)}元附近"
    if price < 100:
        # 取最接近的整数（个位为0更佳，但保持简单）
        return f"{round(price)}元附近"
    # ≥100 取最接近的 5/10
    rounded = int(round(price / 5.0) * 5)
    return f"{rounded}元附近"


def _replace_prices(text: str) -> tuple[str, int]:
    count = 0

    def repl(m: re.Match[str]) -> str:
        nonlocal count
        count += 1
        try:
            return round_price_to_zone(float(m.group(1)))
        except ValueError:
            return m.group(0)

    new_text = _PRICE_RE.sub(repl, text)
    return new_text, count


def sanitize_text(text: str) -> ComplianceResult:
    """对原始交易文字做合规处理。"""
    if not text:
        return ComplianceResult(
            original=text, safe_text="", forbidden_hits=[],
            is_publishable=False, replacements_applied=0,
        )

    # 1. 违规词检测
    hits = [w for w in _FORBIDDEN if w in text]

    # 2. 替换敏感词
    new_text = text
    replace_count = 0
    for pattern, replacement in _REPLACEMENTS:
        new_text, n = re.subn(pattern, replacement, new_text)
        replace_count += n

    # 3. 价格区间化
    new_text, n_prices = _replace_prices(new_text)
    replace_count += n_prices

    return ComplianceResult(
        original=text,
        safe_text=new_text.strip(),
        forbidden_hits=hits,
        is_publishable=len(hits) == 0,
        replacements_applied=replace_count,
    )
