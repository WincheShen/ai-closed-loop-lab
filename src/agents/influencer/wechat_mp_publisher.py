"""WeChat Official Account (公众号) Publisher.

使用微信公众平台 API 发布图文消息。

流程：
1. 通过 appid/appsecret 获取 access_token（2h 有效，自动缓存刷新）
2. 上传文章素材（thumb_media_id 可选）
3. 创建草稿 → 提交发布（或仅草稿等待人工审核）

API 文档：https://developers.weixin.qq.com/doc/offiaccount/Draft_Box/
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

_TOKEN_CACHE: dict[str, Any] = {"token": None, "expires_at": 0}


def _get_access_token() -> str:
    """获取或刷新 access_token。"""
    now = time.time()
    if _TOKEN_CACHE["token"] and _TOKEN_CACHE["expires_at"] > now + 60:
        return _TOKEN_CACHE["token"]

    appid = os.getenv("WX_MP_APPID", "")
    secret = os.getenv("WX_MP_APPSECRET", "")
    if not appid or not secret:
        raise RuntimeError("WX_MP_APPID / WX_MP_APPSECRET 未配置")

    url = "https://api.weixin.qq.com/cgi-bin/token"
    resp = httpx.get(url, params={
        "grant_type": "client_credential",
        "appid": appid,
        "secret": secret,
    }, timeout=10)
    data = resp.json()
    if "access_token" not in data:
        raise RuntimeError(f"获取 access_token 失败: {data}")

    _TOKEN_CACHE["token"] = data["access_token"]
    _TOKEN_CACHE["expires_at"] = now + data.get("expires_in", 7200)
    return _TOKEN_CACHE["token"]


class WechatMpPublisher:
    """微信公众号发布器。"""

    def __init__(self, auto_publish: bool = True) -> None:
        self.auto_publish = auto_publish

    def publish_article(
        self,
        title: str,
        content_html: str,
        author: str = "沈经理AI实验室",
        digest: str = "",
        thumb_media_id: str = "",
    ) -> dict[str, Any]:
        """发布一篇图文到公众号。

        Args:
            title: 文章标题
            content_html: 正文 HTML
            author: 作者名
            digest: 摘要（空则微信自动截取）
            thumb_media_id: 封面图素材 ID（空则无封面）

        Returns:
            {"publish_id": str, "msg_data_id": str | None}
        """
        token = _get_access_token()

        # Step 1: 创建草稿
        draft_id = self._add_draft(
            token, title, content_html, author, digest, thumb_media_id,
        )
        logger.info("草稿已创建: media_id=%s", draft_id)

        if not self.auto_publish:
            return {"publish_id": None, "draft_media_id": draft_id, "status": "draft"}

        # Step 2: 提交发布
        publish_id = self._submit_publish(token, draft_id)
        logger.info("发布提交成功: publish_id=%s", publish_id)

        return {"publish_id": publish_id, "draft_media_id": draft_id, "status": "submitted"}

    def _add_draft(
        self,
        token: str,
        title: str,
        content_html: str,
        author: str,
        digest: str,
        thumb_media_id: str,
    ) -> str:
        """调用草稿箱接口创建草稿，返回 media_id。"""
        url = f"https://api.weixin.qq.com/cgi-bin/draft/add?access_token={token}"
        article = {
            "title": title,
            "author": author,
            "content": content_html,
            "digest": digest or title[:50],
            "content_source_url": "",
            "need_open_comment": 1,
            "only_fans_can_comment": 0,
        }
        if thumb_media_id:
            article["thumb_media_id"] = thumb_media_id

        resp = httpx.post(url, json={"articles": [article]}, timeout=15)
        data = resp.json()
        if "media_id" not in data:
            raise RuntimeError(f"创建草稿失败: {data}")
        return data["media_id"]

    def _submit_publish(self, token: str, media_id: str) -> str:
        """提交发布（异步，微信后台审核后发布）。"""
        url = f"https://api.weixin.qq.com/cgi-bin/freepublish/submit?access_token={token}"
        resp = httpx.post(url, json={"media_id": media_id}, timeout=15)
        data = resp.json()
        if data.get("errcode", 0) != 0:
            raise RuntimeError(f"提交发布失败: {data}")
        return data.get("publish_id", "")

    def upload_thumb(self, image_path: str) -> str:
        """上传封面图，返回 media_id。"""
        token = _get_access_token()
        url = (
            f"https://api.weixin.qq.com/cgi-bin/material/add_material"
            f"?access_token={token}&type=image"
        )
        with open(image_path, "rb") as f:
            resp = httpx.post(url, files={"media": f}, timeout=30)
        data = resp.json()
        if "media_id" not in data:
            raise RuntimeError(f"上传图片失败: {data}")
        return data["media_id"]

    def get_publish_status(self, publish_id: str) -> Optional[dict]:
        """查询发布状态。"""
        token = _get_access_token()
        url = f"https://api.weixin.qq.com/cgi-bin/freepublish/get?access_token={token}"
        resp = httpx.post(url, json={"publish_id": publish_id}, timeout=10)
        data = resp.json()
        if data.get("errcode", 0) != 0:
            return None
        return data
