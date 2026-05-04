"""文字合规处理 smoke test。"""
from __future__ import annotations

from webhook_listener.text_compliance import sanitize_text, round_price_to_zone


def test_basic_replacement():
    res = sanitize_text("今天买入贵州茅台，建仓位置1500元")
    assert "买入" not in res.safe_text
    assert "建仓" not in res.safe_text
    assert "上车" in res.safe_text
    assert res.is_publishable is True


def test_forbidden_word_blocks_publish():
    res = sanitize_text("这只股票必涨，老师推荐")
    assert res.is_publishable is False
    assert "必涨" in res.forbidden_hits
    assert "老师推荐" in res.forbidden_hits


def test_price_zoning():
    assert round_price_to_zone(19.85) == "20元附近"
    assert round_price_to_zone(8.32) == "8元附近"
    assert round_price_to_zone(127.5) == "130元附近"


def test_price_replaced_in_text():
    res = sanitize_text("成本 19.85 目标 23.5")
    assert "19.85" not in res.safe_text
    assert "20元附近" in res.safe_text
    assert "24元附近" in res.safe_text or "25元附近" in res.safe_text  # 23.5 → 24


def test_empty_text():
    res = sanitize_text("")
    assert res.is_publishable is False
