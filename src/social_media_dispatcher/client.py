"""HTTP client to push TopicPayload to Social-media-automation.

SMA 端预期接口：
    POST {base_url}/api/tasks
    Body: TopicPayload (JSON)
    Resp: {success, sma_task_id, sma_status, ...}
"""
from __future__ import annotations

import logging
import os
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
    ):
        self.base_url = (
            base_url
            or os.environ.get("SMA_BASE_URL")
            or "http://127.0.0.1:8003"
        ).rstrip("/")
        self.api_token = api_token or os.environ.get("SMA_API_TOKEN")
        self.timeout = timeout

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
        url = f"{self.base_url}/api/tasks"
        headers = {"Content-Type": "application/json"}
        if self.api_token:
            headers["Authorization"] = f"Bearer {self.api_token}"

        try:
            resp = httpx.post(
                url,
                content=payload.model_dump_json(),
                headers=headers,
                timeout=self.timeout,
            )
        except httpx.HTTPError as e:
            logger.warning("SMA dispatch network error: %s", e)
            return DispatchResult(success=False, error=str(e))

        body: dict = {}
        try:
            body = resp.json()
        except Exception:  # noqa: BLE001
            body = {"raw": resp.text}

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
