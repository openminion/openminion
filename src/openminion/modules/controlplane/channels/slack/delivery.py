"""Slack outbound delivery."""

from __future__ import annotations

import time
from typing import Any, Callable

from openminion.modules.controlplane.channels.slack.bot_api import (
    SlackAPIError,
    SlackWebAPI,
)
from openminion.modules.controlplane.channels.slack.config import SlackDeliveryConfig
from openminion.modules.controlplane.channels.slack.constants import CHANNEL_ID
from openminion.modules.controlplane.channels.slack.models import (
    SlackDeliveryResult,
    SlackReplyTarget,
)
from openminion.modules.controlplane.contracts.models import DeliveryContext
from openminion.modules.controlplane.interfaces import CONTROLPLANE_INTERFACE_VERSION

class SlackDeliveryService:
    contract_version = CONTROLPLANE_INTERFACE_VERSION

    def __init__(
        self,
        *,
        api: SlackWebAPI,
        delivery_config: SlackDeliveryConfig | None = None,
        audit_logger: Any | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        self._api = api
        self._delivery_config = delivery_config or SlackDeliveryConfig()
        self._audit_logger = audit_logger
        self._sleep = sleep_fn

    def send_payload(
        self, payload: Any, target: SlackReplyTarget | DeliveryContext | dict[str, Any]
    ) -> SlackDeliveryResult:
        text = str(getattr(payload, "text", "") or payload.get("text", "") if isinstance(payload, dict) else getattr(payload, "text", ""))
        blocks = _extract_blocks(payload)
        return self.send_text(text, target, blocks=blocks)

    def send_text(
        self,
        text: str,
        target: SlackReplyTarget | DeliveryContext | dict[str, Any],
        *,
        blocks: list[dict[str, Any]] | None = None,
    ) -> SlackDeliveryResult:
        reply_target = _resolve_reply_target(target)
        chunks = _split_text(str(text or ""), self._delivery_config.max_message_chars)
        last_ts: str | None = None
        for index, chunk in enumerate(chunks):
            payload: dict[str, Any] = {
                "channel": reply_target.channel_id,
                "text": chunk or " ",
            }
            if reply_target.thread_ts:
                payload["thread_ts"] = reply_target.thread_ts
            if blocks and index == 0:
                payload["blocks"] = blocks
            response = self._call_with_retry(payload)
            last_ts = str(response.get("ts") or "") or last_ts
        return SlackDeliveryResult(
            ok=True,
            message_ts=last_ts,
            channel_id=reply_target.channel_id,
            chunks_sent=len(chunks),
        )

    def _call_with_retry(self, payload: dict[str, Any]) -> dict[str, Any]:
        attempts = max(1, self._delivery_config.retry.max_attempts)
        for attempt in range(1, attempts + 1):
            try:
                return self._api.chat_post_message(payload)
            except SlackAPIError as exc:
                self._audit_failure(exc, attempt=attempt)
                if not exc.retryable or attempt >= attempts:
                    raise
                self._sleep(exc.retry_after_seconds or self._delivery_config.retry.backoff_seconds)
        raise AssertionError("unreachable Slack delivery retry state")

    def _audit_failure(self, exc: SlackAPIError, *, attempt: int) -> None:
        if self._audit_logger is None:
            return
        details = {
            "channel": CHANNEL_ID,
            "error_code": exc.error_code,
            "retryable": exc.retryable,
            "attempt": attempt,
        }
        if hasattr(self._audit_logger, "emit"):
            self._audit_logger.emit(
                "cp.delivery.failed",
                outcome="error",
                severity="warning" if exc.retryable else "error",
                details=details,
            )

    def deliver(
        self, payload: Any, ctx: SlackReplyTarget | DeliveryContext | dict[str, Any]
    ) -> SlackDeliveryResult:
        return self.send_payload(payload, ctx)


def _extract_blocks(payload: Any) -> list[dict[str, Any]] | None:
    metadata = getattr(payload, "metadata", None)
    if isinstance(payload, dict):
        metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        return None
    blocks = metadata.get("slack_blocks") or metadata.get("blocks")
    if not isinstance(blocks, list):
        return None
    return [block for block in blocks if isinstance(block, dict)]


def _resolve_reply_target(
    target: SlackReplyTarget | DeliveryContext | dict[str, Any]
) -> SlackReplyTarget:
    if isinstance(target, SlackReplyTarget):
        return target
    if isinstance(target, DeliveryContext):
        return SlackReplyTarget(
            channel_id=str(target.chat_id),
            thread_ts=target.thread_id,
            reply_to=target.reply_to,
        )
    channel_id = str(target.get("channel_id") or target.get("chat_id") or "").strip()
    if not channel_id:
        raise ValueError("missing Slack channel_id for delivery")
    return SlackReplyTarget(
        channel_id=channel_id,
        thread_ts=str(target.get("thread_ts") or target.get("thread_id") or "").strip()
        or None,
        reply_to=str(target.get("reply_to") or "").strip() or None,
    )


def _split_text(text: str, limit: int) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    start = 0
    while start < len(text):
        chunks.append(text[start : start + limit])
        start += limit
    return chunks
