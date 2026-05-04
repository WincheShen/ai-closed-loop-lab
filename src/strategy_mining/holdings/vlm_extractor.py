"""VLM-based extractor for broker-app holding screenshots.

Handles the specific 同花顺 / 湘财证券 / 中信证券 / 等 Chinese broker mobile-app
"持仓" (holdings) page screenshots collected from WeChat Official Account
article comments. The screenshots often:

- Have 淘股吧 / "@二池战绩验证" watermarks (must be ignored).
- Have stock names partially painted-over by hand (red/black scribbles)
  obscuring 2-4 trailing characters while keeping the first 2-3 characters
  readable.
- Occasionally are NOT holding pages (e.g. 战绩验证 list page) — the
  extractor must classify page_type.

The extractor returns a dict with a stable schema so downstream code
can write to `strategy_mining.holding_snapshots` and `holding_items`
without further parsing.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openai import OpenAI

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prompt — 中文, 精确, 面向 gpt-4o vision
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """你是券商 App 持仓截图的结构化抽取专家。用户会发给你一张来自手机券商 App 的截图。

你必须：
1. 忽略所有水印，尤其是『淘股吧』『@二池(战绩验证)』『二池战绩验证』等红色或灰色倾斜文字。
2. 识别股票名是否被手画线条（红色或黑色涂鸦，非原 App 元素）遮挡，并如实汇报可见片段。
3. 对数字严格 OCR，不要推测；看不清的字段设为 null。
4. 以严格 JSON 返回，不要 Markdown 代码块，不要多余说明。
5. 截图可能并非持仓页面，必须先判定 page_type。

输出 JSON Schema:
{
  "page_type": "position_page" | "performance_page" | "other",
  "broker_name": "如 '中信证券' / '湘财证券' / null",
  "account_suffix": "账号后 4 位，如 '3575' / '6678' / null",
  "summary": {
    "total_assets": 数字或 null,
    "total_pnl": 数字或 null,
    "total_pnl_today": 数字或 null,
    "total_pnl_today_pct": 比例小数 (0.0234 表示 2.34%) 或 null,
    "market_value": 数字或 null,
    "available_cash": 数字或 null,
    "withdrawable_cash": 数字或 null,
    "position_pct": 比例小数 (0.729 表示 72.9%) 或 null
  },
  "holdings": [
    {
      "row_index": 0,
      "stock_name_visible": "可见的姓名全部字符，例如 '广东华' 或 '东芯股份'",
      "stock_name_obfuscation": "manual_paint" | "auto_blur" | "none",
      "stock_name_visible_chars": 可见字数,
      "market_value": 数字或 null,
      "pnl_amount": 数字或 null,
      "pnl_pct": 比例小数或 null,
      "position_qty": 整数或 null,
      "available_qty": 整数或 null,
      "cost_price": 数字或 null,
      "current_price": 数字或 null,
      "is_zero_position": true/false,
      "row_color_hint": "red"/"blue"/null  -- 盈红亏蓝
    }
  ],
  "performance_entries": [  // 仅 page_type=performance_page 时填写
    {
      "date": "20260317",
      "pnl_pct": -0.0433,
      "amount_cn": "81.52万元",
      "stock_name_full": "东芯股份",
      "symbol": "688110",
      "exchange": "SH"
    }
  ],
  "extractor_confidence": 0.0-1.0,
  "notes": "任何异常说明"
}

重要规则：
- 若某行 持仓/可用 都是 0，必须设 is_zero_position=true（是『已卖出但 T+1 仍显示』的证据）。
- 若行里颜色偏红（盈利/上涨），row_color_hint='red'；偏蓝（亏损/下跌）='blue'。
- 比例一律用小数（百分比 / 100），例如 63.14% -> 0.6314。
- 金额按截图实际单位，不要换算。如截图显示 "1,033,050.66" 就输出 1033050.66。
- 若 stock_name_visible 只看到前几个字（因手画涂线），保留看到的全部，不要猜全名。
- performance_page 页面没有 holdings 列表，holdings 字段返回 []。
"""

USER_INSTRUCTION = "请按 system 指令解析这张图。"


# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class VLMResult:
    """Structured return from extract()."""

    page_type: str
    broker_name: str | None
    account_suffix: str | None
    summary: dict[str, Any]
    holdings: list[dict[str, Any]]
    performance_entries: list[dict[str, Any]]
    extractor_confidence: float
    notes: str | None
    raw_json: dict[str, Any]
    model: str


# ---------------------------------------------------------------------------
def _load_image_data_url(path: Path) -> str:
    ext = path.suffix.lower().lstrip(".")
    mime = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png", "webp": "webp"}.get(ext, "jpeg")
    b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:image/{mime};base64,{b64}"


def _strip_json_fence(text: str) -> str:
    """Accept occasional ```json blocks even though system asked for raw."""
    t = text.strip()
    if t.startswith("```"):
        # remove opening fence
        t = re.sub(r"^```[a-zA-Z]*\n?", "", t)
        t = re.sub(r"\n?```\s*$", "", t)
    return t.strip()


def extract(image_path: str | Path, *, model: str | None = None) -> VLMResult:
    """Run VLM extraction against a single screenshot.

    Raises:
        FileNotFoundError: if image_path does not exist.
        RuntimeError: on non-JSON model output.
    """
    p = Path(image_path)
    if not p.is_file():
        raise FileNotFoundError(p)

    client = OpenAI()  # relies on OPENAI_API_KEY / OPENAI_BASE_URL env vars
    model = model or os.environ.get("HOLDINGS_VLM_MODEL") or "gpt-5.3-chat"

    data_url = _load_image_data_url(p)
    # Some Azure deployments (e.g. gpt-5.x) use max_completion_tokens; classic
    # OpenAI uses max_tokens. Omit both — default is fine for our JSON size.
    resp = client.chat.completions.create(
        model=model,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": USER_INSTRUCTION},
                    {"type": "image_url", "image_url": {"url": data_url, "detail": "high"}},
                ],
            },
        ],
    )
    content = resp.choices[0].message.content or ""
    content = _strip_json_fence(content)
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        logger.error("VLM returned non-JSON: %s", content[:500])
        raise RuntimeError(f"VLM non-JSON output: {exc}") from exc

    return VLMResult(
        page_type=data.get("page_type", "other"),
        broker_name=data.get("broker_name"),
        account_suffix=data.get("account_suffix"),
        summary=data.get("summary") or {},
        holdings=data.get("holdings") or [],
        performance_entries=data.get("performance_entries") or [],
        extractor_confidence=float(data.get("extractor_confidence") or 0.0),
        notes=data.get("notes"),
        raw_json=data,
        model=model,
    )
