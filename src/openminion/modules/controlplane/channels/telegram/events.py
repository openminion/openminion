from typing import Any, Protocol

from openminion.modules.controlplane.interfaces import CONTROLPLANE_INTERFACE_VERSION


class SessionEventSink(Protocol):
    contract_version: str

    def record_inbound(self, event: Any, raw_update: dict[str, Any]) -> None: ...

    def record_outbound(
        self,
        *,
        session_id: str | None,
        chat_id: str,
        topic_id: str | None,
        payload: dict[str, Any],
        telegram_message: dict[str, Any],
    ) -> None: ...


class NoopSessionEventSink:
    contract_version = CONTROLPLANE_INTERFACE_VERSION

    def record_inbound(self, event: Any, raw_update: dict[str, Any]) -> None:
        return

    def record_outbound(
        self,
        *,
        session_id: str | None,
        chat_id: str,
        topic_id: str | None,
        payload: dict[str, Any],
        telegram_message: dict[str, Any],
    ) -> None:
        return
