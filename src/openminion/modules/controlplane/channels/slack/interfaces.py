"""Protocol surfaces for Slack-local adapter dependencies."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from openminion.modules.controlplane.interfaces import CONTROLPLANE_INTERFACE_VERSION


@runtime_checkable
class SlackWebAPIProtocol(Protocol):
    contract_version: str

    def auth_test(self) -> dict[str, Any]: ...

    def chat_post_message(self, payload: dict[str, Any]) -> dict[str, Any]: ...


@runtime_checkable
class SlackStateStoreAPI(Protocol):
    contract_version: str

    def mark_event_seen(self, event_id: str) -> bool: ...

    def close(self) -> None: ...


@runtime_checkable
class SlackRuntimeHandlerAPI(Protocol):
    contract_version: str

    def handle_inbound(self, inbound: Any) -> Any: ...


def ensure_slack_component_compatibility(
    component: object, *, component_type: str
) -> None:
    version = getattr(component, "contract_version", None)
    if version != CONTROLPLANE_INTERFACE_VERSION:
        raise TypeError(
            f"{component_type} must expose contract_version={CONTROLPLANE_INTERFACE_VERSION!r}"
        )
    required = {
        "bot_api": ("auth_test", "chat_post_message"),
        "state_store": ("mark_event_seen", "close"),
        "runtime_handler": ("handle_inbound",),
    }.get(component_type, ())
    missing = [name for name in required if not callable(getattr(component, name, None))]
    if missing:
        raise TypeError(f"{component_type} missing methods: {', '.join(missing)}")
