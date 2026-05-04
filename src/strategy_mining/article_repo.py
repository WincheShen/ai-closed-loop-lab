"""Repository helpers for ``strategy_mining.wechat_articles``.

Pure-SQL upsert API used by the URL lister and the (future) content fetcher.
Avoids ORMs deliberately to keep schema migrations explicit and SQL visible.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Iterable

from psycopg2.extras import Json, execute_values

from .db import connection_scope


def _from_unix(ts: int | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc)
    except (TypeError, ValueError):
        return None


def upsert_url_listing(
    rows: Iterable[dict[str, Any]],
    *,
    account_name: str,
    biz: str = "",
) -> int:
    """Insert URL-list entries returned by ``PublicAccountsWeb.get_urls``.

    Each row is a thin record (URL + title + timestamps), no body yet.
    Body is filled by a separate fetcher in a later step. Conflicts on
    ``article_url`` keep the existing record but refresh ``raw_meta`` and
    timestamps so re-listing the same account is idempotent.

    Returns the number of rows attempted (not necessarily inserted).
    """
    payload: list[tuple] = []
    for r in rows:
        link = r.get("link")
        if not link:
            continue
        payload.append(
            (
                link,
                r.get("aid") or r.get("appmsgid"),
                biz or r.get("biz") or "",
                account_name,
                r.get("title") or "",
                r.get("author"),
                _from_unix(r.get("update_time") or r.get("create_time")),
                "url_listing",
                Json(r),
            )
        )
    if not payload:
        return 0

    sql = """
        INSERT INTO strategy_mining.wechat_articles
            (article_url, msg_id, biz, account_name, title, author,
             publish_time, fetch_method, raw_meta)
        VALUES %s
        ON CONFLICT (article_url) DO UPDATE SET
            account_name = EXCLUDED.account_name,
            title        = COALESCE(NULLIF(EXCLUDED.title, ''),
                                    strategy_mining.wechat_articles.title),
            publish_time = COALESCE(strategy_mining.wechat_articles.publish_time,
                                    EXCLUDED.publish_time),
            raw_meta     = EXCLUDED.raw_meta;
    """
    with connection_scope() as conn, conn.cursor() as cur:
        execute_values(cur, sql, payload, page_size=200)
    return len(payload)


def list_articles_without_body(limit: int = 100) -> list[dict[str, Any]]:
    """Return articles that have URL+meta but no fetched content yet."""
    with connection_scope(autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, article_url, account_name, title, publish_time
              FROM strategy_mining.wechat_articles
             WHERE content_text IS NULL OR content_text = ''
             ORDER BY publish_time DESC NULLS LAST
             LIMIT %s
            """,
            (limit,),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
