"""List article URLs of a target WeChat Official Account via mp.weixin.qq.com backend.

Uses a personal subscription account's session (cookie + token) and the
``wechatarticles.PublicAccountsWeb`` client to page through any target
公众号's history articles.

Requirements:
- Environment vars ``WX_MP_TOKEN`` and ``WX_MP_COOKIE`` set.
- Target ``nickname`` must be the exact display name of the public account.
- Optional ``biz`` (the ``__biz`` query parameter) speeds up matching.

Usage:
    >>> from strategy_mining.wechat_url_lister import list_articles
    >>> rows = list_articles(nickname="某公众号", max_count=200)
    >>> rows[0]
    {'aid': '...', 'title': '...', 'link': 'https://mp.weixin.qq.com/s?...',
     'create_time': 1700000000, 'update_time': 1700000000, 'cover': '...'}

Pagination is handled by stepping ``begin`` in increments of 5 (the backend's
maximum page size) with a configurable sleep between calls. Be conservative —
the backend bans the account for 24h if you go too fast.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any

logger = logging.getLogger(__name__)


def _client():
    from wechatarticles import PublicAccountsWeb  # local import: optional dep

    cookie = os.environ.get("WX_MP_COOKIE")
    token = os.environ.get("WX_MP_TOKEN")
    if not cookie or not token:
        raise RuntimeError(
            "WX_MP_COOKIE and WX_MP_TOKEN must be set in env / .env"
        )
    return PublicAccountsWeb(cookie=cookie, token=token)


def list_articles(
    nickname: str,
    biz: str = "",
    max_count: int = 50,
    page_size: int = 5,
    sleep_seconds: float = 3.0,
) -> list[dict[str, Any]]:
    """Page through articles of one public account.

    Args:
        nickname: Display name of the target public account (exact match).
        biz: Optional ``__biz`` value; speeds up matching when supplied.
        max_count: Stop after collecting this many article entries.
        page_size: Backend max is 5; do not raise above 5.
        sleep_seconds: Sleep between page requests to avoid bans (>=2s).

    Returns:
        A list of dicts with keys: ``aid``, ``title``, ``link``,
        ``create_time``, ``update_time``, ``cover``, ``digest``, ``itemidx``.
    """
    paw = _client()
    collected: list[dict[str, Any]] = []
    begin = 0
    while len(collected) < max_count:
        try:
            # NOTE: PyPI wechatarticles 0.7.0 get_urls signature is
            # (nickname, begin=0, count=5); `biz` is not accepted.
            page = paw.get_urls(
                nickname,
                begin=str(begin),
                count=str(page_size),
            )
        except Exception as exc:  # pragma: no cover - network errors
            logger.exception("get_urls failed at begin=%s: %s", begin, exc)
            raise
        if not page:
            logger.info("no more articles after begin=%s", begin)
            break
        collected.extend(page)
        logger.info("fetched %d (total=%d) from begin=%s",
                    len(page), len(collected), begin)
        begin += page_size
        time.sleep(sleep_seconds)
    return collected[:max_count]
