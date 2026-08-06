"""HTTP client to push TopicPayload to Social-media-automation.

SMA 端预期接口：
    POST {base_url}/api/tasks
    Body: TopicPayload (JSON)
    Resp: {success, sma_task_id, sma_status, ...}
"""
from __future__ import annotations

import logging
import os
import time
from typing import Optional

import httpx

from .schemas import DispatchResult, TopicPayload

logger = logging.getLogger(__name__)


class SmaClientError(Exception):
    pass


class SmaClient:
    """与 Social-media-automation 通信的 HTTP 客户端。"""

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_token: Optional[str] = None,
        timeout: float = 10.0,
        max_retries: int = 3,
    ):
        self.base_url = (
            base_url
            or os.environ.get("SMA_BASE_URL")
            or "http://127.0.0.1:8003"
        ).rstrip("/")
        self.api_token = api_token or os.environ.get("SMA_API_TOKEN")
        self.timeout = timeout
        self.max_retries = max_retries

    # ------------------------------------------------------------------

    def health(self) -> dict:
        url = f"{self.base_url}/health"
        try:
            resp = httpx.get(url, timeout=self.timeout)
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as e:
            raise SmaClientError(f"SMA health check failed: {e}") from e

    def dispatch(self, payload: TopicPayload) -> DispatchResult:
        """发送 topic 到 SMA，带指数退避重试。"""
        url = f"{self.base_url}/api/tasks"
        headers = {"Content-Type": "application/json"}
        if self.api_token:
            headers["Authorization"] = f"Bearer {self.api_token}"

        last_error = None
        for attempt in range(self.max_retries):
            try:
                resp = httpx.post(
                    url,
                    content=payload.model_dump_json(),
                    headers=headers,
                    timeout=self.timeout,
                )
            except httpx.HTTPError as e:
                last_error = e
                if attempt < self.max_retries - 1:
                    wait = 2 ** attempt
                    logger.warning(
                        "SMA dispatch 重试 %d/%d (等待 %ds): %s",
                        attempt + 1, self.max_retries, wait, e,
                    )
                    time.sleep(wait)
                    continue
                return DispatchResult(success=False, error=f"网络错误(重试{self.max_retries}次): {e}")

            body: dict = {}
            try:
                body = resp.json()
            except Exception:  # noqa: BLE001
                body = {"raw": resp.text}

            if resp.status_code >= 500 and attempt < self.max_retries - 1:
                wait = 2 ** attempt
                logger.warning(
                    "SMA 服务端错误 %d，重试 %d/%d (等待 %ds)",
                    resp.status_code, attempt + 1, self.max_retries, wait,
                )
                time.sleep(wait)
                continue

            if resp.status_code >= 400:
                return DispatchResult(
                    success=False,
                    error=f"HTTP {resp.status_code}: {body}",
                    response_body=body,
                )

            return DispatchResult(
                success=True,
                sma_task_id=body.get("task_id"),
                sma_status=body.get("status"),
                response_body=body,
            )

        return DispatchResult(success=False, error=f"重试耗尽: {last_error}")
