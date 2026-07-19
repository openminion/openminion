"""Slack Socket Mode runner."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Protocol

from openminion.modules.controlplane.channels.slack.config import SlackChannelConfig
from openminion.modules.controlplane.channels.slack.constants import (
    CHANNEL_ID,
)
from openminion.modules.controlplane.channels.slack.normalization import (
    envelope_from_event_callback,
    event_callback_from_payload,
)
from openminion.modules.controlplane.channels.slack.runtime.helpers import (
    process_envelope,
)
from openminion.modules.controlplane.channels.slack.slash_commands import (
    inbound_from_slash,
    parse_slash_payload,
)
from openminion.modules.controlplane.contracts.models import DeliveryContext
from openminion.modules.controlplane.interfaces import CONTROLPLANE_INTERFACE_VERSION


class SlackSocketClientAPI(Protocol):
    def connect(self) -> None: ...

    def recv(self, timeout: float = 1.0) -> dict[str, Any] | None: ...

    def ack(self, envelope_id: str) -> None: ...

    def close(self) -> None: ...


class MissingSlackSocketDependency(RuntimeError):
    pass


class SlackSocketModeRunner:
    contract_version = CONTROLPLANE_INTERFACE_VERSION
    channel_id = CHANNEL_ID

    def __init__(
        self,
        *,
        config: SlackChannelConfig,
        runtime: Any,
        delivery: Any,
        socket_client: SlackSocketClientAPI | None = None,
        state_store: Any | None = None,
        audit_logger: Any | None = None,
        logger: logging.Logger | None = None,
        store: Any | None = None,
        outbox_worker: Any | None = None,
        bot_user_id: str | None = None,
        sleep_fn=time.sleep,
    ) -> None:
        self._config = config
        self._runtime = runtime
        self._delivery = delivery
        self._socket_client = socket_client
        self._state_store = state_store
        self._audit_logger = audit_logger
        self._log = logger or logging.getLogger(__name__)
        self._store = store
        self._outbox_worker = outbox_worker
        self._bot_user_id = bot_user_id
        self._sleep = sleep_fn
        self._connected = False
        self._outbox_managed_by_supervisor = False

    def start(self, stop_event: Any | None = None) -> None:
        if self._socket_client is None:
            raise MissingSlackSocketDependency(
                "Slack Socket Mode requires a SlackSocketClientAPI implementation; "
                "install openminion[slack] or inject a client."
            )
        event = stop_event if stop_event is not None else threading.Event()
        self._socket_client.connect()
        self._connected = True
        try:
            while not event.is_set():
                envelope = self._socket_client.recv(timeout=1.0)
                if envelope is None:
                    continue
                self._handle_socket_envelope(envelope)
        finally:
            self._socket_client.close()
            self._connected = False

    def stop(self) -> None:
        if self._socket_client is not None:
            self._socket_client.close()
        self._connected = False
        close = getattr(self._state_store, "close", None)
        if callable(close):
            close()

    def deliver(self, payload: dict[str, Any], ctx: DeliveryContext) -> Any:
        return self._delivery.deliver(payload, ctx)

    def health(self) -> dict[str, Any]:
        return {
            "ok": self._connected,
            "mode": "socket",
            "connected": self._connected,
        }

    def _handle_socket_envelope(self, envelope: dict[str, Any]) -> None:
        envelope_id = str(envelope.get("envelope_id") or "").strip()
        if envelope_id:
            self._socket_client.ack(envelope_id)  # type: ignore[union-attr]
        payload = envelope.get("payload")
        if not isinstance(payload, dict):
            return
        if envelope.get("type") == "slash_commands":
            slash = parse_slash_payload(payload)
            inbound = inbound_from_slash(slash)
            result = self._runtime.handle_inbound(inbound)
            if isinstance(result, dict):
                self._delivery.deliver(
                    result,
                    {
                        "channel_id": slash.channel_id,
                    },
                )
            return
        callback = event_callback_from_payload(payload)
        if callback is None:
            return
        slack_event = envelope_from_event_callback(
            callback,
            bot_user_id=self._bot_user_id,
            allow_broad_channel_messages=self._config.access.allow_broad_channel_messages,
        )
        if slack_event is not None:
            process_envelope(self, slack_event)
