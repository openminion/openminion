from typing import Any, Protocol

from openminion.modules.controlplane.contracts.models import (
    DeliveryContext,
    InboundMessage,
)


class Adapter(Protocol):
    contract_version: str

    def start(self) -> None: ...


class ChannelAdapter(Protocol):
    contract_version: str
    channel_id: str

    def start(self, stop_event: Any | None = None) -> None: ...

    def deliver(self, payload: dict[str, Any], ctx: DeliveryContext) -> Any: ...


class InboundHandler(Protocol):
    contract_version: str

    def handle_inbound(self, inbound: InboundMessage) -> dict | None: ...
