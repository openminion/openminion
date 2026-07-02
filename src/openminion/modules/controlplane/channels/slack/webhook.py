"""Slack HTTP Events API runner."""

from __future__ import annotations

import logging
import threading
from typing import Any, Mapping

from openminion.modules.controlplane.channels.slack.config import SlackChannelConfig
from openminion.modules.controlplane.channels.slack.constants import (
    CHANNEL_ID,
)
from openminion.modules.controlplane.channels.slack.listener import (
    parse_json_body,
    url_verification_response,
    verify_slack_signature,
)
from openminion.modules.controlplane.channels.slack.normalization import (
    envelope_from_event_callback,
    event_callback_from_payload,
)
from openminion.modules.controlplane.channels.slack.runtime.helpers import (
    process_envelope,
)
from openminion.modules.controlplane.contracts.models import DeliveryContext
from openminion.modules.controlplane.interfaces import CONTROLPLANE_INTERFACE_VERSION


class SlackHttpEventsRunner:
    contract_version = CONTROLPLANE_INTERFACE_VERSION
    channel_id = CHANNEL_ID

    def __init__(
        self,
        *,
        config: SlackChannelConfig,
        runtime: Any,
        delivery: Any,
        state_store: Any | None = None,
        audit_logger: Any | None = None,
        logger: logging.Logger | None = None,
        store: Any | None = None,
        outbox_worker: Any | None = None,
        bot_user_id: str | None = None,
    ) -> None:
        self._config = config
        self._runtime = runtime
        self._delivery = delivery
        self._state_store = state_store
        self._audit_logger = audit_logger
        self._log = logger or logging.getLogger(__name__)
        self._store = store
        self._outbox_worker = outbox_worker
        self._bot_user_id = bot_user_id

    def start(self, stop_event: Any | None = None) -> None:
        # The HTTP server is owned by deployment glue. This adapter exposes the
        # signed request handler and keeps the worker lifecycle consistent.
        if self._outbox_worker is not None:
            self._outbox_worker.run_once()
        event = stop_event if stop_event is not None else threading.Event()
        while not event.is_set():
            event.wait(0.2)

    def stop(self) -> None:
        close = getattr(self._state_store, "close", None)
        if callable(close):
            close()

    def deliver(self, payload: dict[str, Any], ctx: DeliveryContext) -> Any:
        return self._delivery.deliver(payload, ctx)

    def handle_http_event(
        self,
        body: bytes | str | Mapping[str, Any],
        *,
        headers: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        body_bytes = body if isinstance(body, bytes) else str(body).encode("utf-8")
        if headers is not None:
            verify_slack_signature(
                signing_secret=self._config.signing_secret,
                timestamp=str(
                    headers.get("X-Slack-Request-Timestamp")
                    or headers.get("x-slack-request-timestamp")
                    or ""
                ),
                body=body_bytes,
                signature=str(
                    headers.get("X-Slack-Signature")
                    or headers.get("x-slack-signature")
                    or ""
                ),
            )
        payload = parse_json_body(body)
        challenge = url_verification_response(payload)
        if challenge is not None:
            return challenge
        callback = event_callback_from_payload(payload)
        if callback is None:
            return {"status": 200, "body": ""}
        envelope = envelope_from_event_callback(
            callback,
            bot_user_id=self._bot_user_id,
            allow_broad_channel_messages=self._config.access.allow_broad_channel_messages,
        )
        if envelope is not None:
            process_envelope(self, envelope)
        return {"status": 200, "body": ""}

    def health(self) -> dict[str, Any]:
        return {"ok": True, "mode": "http"}
