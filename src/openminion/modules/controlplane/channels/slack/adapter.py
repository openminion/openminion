"""Slack adapter construction helpers."""

from __future__ import annotations

import logging
import queue
from typing import Any

from openminion.modules.controlplane.channels.slack.config import SlackChannelConfig
from openminion.modules.controlplane.channels.slack.delivery import SlackDeliveryService
from openminion.modules.controlplane.channels.slack.socket_mode import (
    MissingSlackSocketDependency,
    SlackSocketClientAPI,
    SlackSocketModeRunner,
)
from openminion.modules.controlplane.channels.slack.webhook import SlackHttpEventsRunner


def build_slack_runner(
    *,
    config: SlackChannelConfig,
    runtime: Any,
    delivery: SlackDeliveryService,
    state_store: Any | None = None,
    audit_logger: Any | None = None,
    logger: logging.Logger | None = None,
    store: Any | None = None,
    outbox_worker: Any | None = None,
    bot_user_id: str | None = None,
    socket_client: SlackSocketClientAPI | None = None,
) -> SlackSocketModeRunner | SlackHttpEventsRunner:
    if config.mode == "http":
        return SlackHttpEventsRunner(
            config=config,
            runtime=runtime,
            delivery=delivery,
            state_store=state_store,
            audit_logger=audit_logger,
            logger=logger,
            store=store,
            outbox_worker=outbox_worker,
            bot_user_id=bot_user_id,
        )
    client = socket_client or _maybe_sdk_socket_client(config)
    return SlackSocketModeRunner(
        config=config,
        runtime=runtime,
        delivery=delivery,
        socket_client=client,
        state_store=state_store,
        audit_logger=audit_logger,
        logger=logger,
        store=store,
        outbox_worker=outbox_worker,
        bot_user_id=bot_user_id,
    )


class SlackSdkSocketClient:
    """Tiny adapter around slack_sdk SocketModeClient.

    The import remains inside this Slack-local file so the core package never
    depends on Slack SDK classes unless the optional extra is installed.
    """

    def __init__(self, *, app_token: str, bot_token: str) -> None:
        try:
            from slack_sdk.socket_mode import SocketModeClient
            from slack_sdk.socket_mode.response import SocketModeResponse
            from slack_sdk.web import WebClient
        except Exception as exc:  # pragma: no cover - optional dependency
            raise MissingSlackSocketDependency(
                "Slack Socket Mode requires openminion[slack]."
            ) from exc
        self._SocketModeResponse = SocketModeResponse
        self._queue: queue.Queue[dict[str, Any]] = queue.Queue()
        self._client = SocketModeClient(
            app_token=app_token,
            web_client=WebClient(token=bot_token),
        )
        self._client.socket_mode_request_listeners.append(self._on_request)

    def connect(self) -> None:
        self._client.connect()

    def recv(self, timeout: float = 1.0) -> dict[str, Any] | None:
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def ack(self, envelope_id: str) -> None:
        response = self._SocketModeResponse(envelope_id=envelope_id)
        self._client.send_socket_mode_response(response)

    def close(self) -> None:
        self._client.close()

    def _on_request(self, _client: Any, request: Any) -> None:
        self._queue.put(
            {
                "envelope_id": getattr(request, "envelope_id", ""),
                "type": getattr(request, "type", ""),
                "payload": getattr(request, "payload", {}),
            }
        )


def _maybe_sdk_socket_client(config: SlackChannelConfig) -> SlackSocketClientAPI | None:
    if not config.app_token:
        return None
    return SlackSdkSocketClient(app_token=config.app_token, bot_token=config.bot_token)
