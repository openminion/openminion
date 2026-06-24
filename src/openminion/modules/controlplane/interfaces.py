from typing import Any, Protocol, runtime_checkable

from .constants import PRINCIPAL_BINDING_STATUS_ACTIVE
from .contracts.models import DeliveryContext

CONTROLPLANE_INTERFACE_VERSION = "v1"


@runtime_checkable
class SessionStoreAPI(Protocol):
    contract_version: str

    def resolve_session(self, user_key: str, chat_key: str) -> str: ...

    def resolve_agent(self, session_id: str) -> str: ...

    def bind_session(self, user_key: str, chat_key: str, session_id: str) -> None: ...

    def session_owner(self, session_id: str) -> str | None: ...

    def bind_session_owned(
        self,
        *,
        user_key: str,
        chat_key: str,
        session_id: str,
        is_admin: bool,
    ) -> bool: ...

    def ensure_agent(self, agent_id: str, name: str | None = None) -> None: ...

    def persist_inbound(self, inbound: Any, session_id: str) -> None: ...

    def attachment_refs_from_inputs(self, inputs: list[Any]) -> list[str]: ...

    def append_turn(
        self,
        *,
        session_id: str,
        role: str,
        content: str,
        attachments: list[str] | None = None,
        meta: dict[str, Any] | None = None,
    ) -> str: ...

    def set_pending_clarify(self, session_id: str, payload: dict[str, Any]) -> None: ...

    def get_pending_clarify(self, session_id: str) -> dict[str, Any] | None: ...

    def clear_pending_clarify(self, session_id: str) -> None: ...

    def list_pending_clarifies(self) -> list[dict[str, Any]]: ...


@runtime_checkable
class RouterAPI(Protocol):
    contract_version: str

    def resolve(self, inbound: Any) -> Any: ...


@runtime_checkable
class CommandParserAPI(Protocol):
    contract_version: str

    def parse(self, text: str) -> Any: ...


@runtime_checkable
class BrainClientAPI(Protocol):
    contract_version: str

    def run(
        self,
        *,
        session_id: str,
        agent_id: str,
        user_text: str | None,
        attachment_refs: list[str],
        trace_id: str,
    ) -> dict[str, Any]: ...


@runtime_checkable
class OutboundSenderAPI(Protocol):
    contract_version: str

    def __call__(self, payload: dict[str, Any]) -> None: ...


@runtime_checkable
class InboundHandlerAPI(Protocol):
    contract_version: str

    def handle_inbound(self, inbound: Any) -> dict[str, Any] | None: ...


@runtime_checkable
class AdapterAPI(Protocol):
    contract_version: str

    def start(self) -> None: ...


@runtime_checkable
class ChannelAdapterAPI(Protocol):
    contract_version: str
    channel_id: str

    def start(self, stop_event: Any | None = None) -> None: ...

    def deliver(self, payload: dict[str, Any], ctx: DeliveryContext) -> Any: ...


@runtime_checkable
class ChannelRegistryAPI(Protocol):
    contract_version: str

    def register(self, adapter: ChannelAdapterAPI) -> None: ...

    def get(self, channel_id: str) -> ChannelAdapterAPI: ...

    def start_all(self, stop_event: Any | None = None) -> dict[str, Any]: ...

    def stop_all(self) -> dict[str, Any]: ...

    def health(self) -> dict[str, dict[str, Any]]: ...

    def list(self) -> list[str]: ...


@runtime_checkable
class SessionEventSinkAPI(Protocol):
    contract_version: str

    def record_inbound(self, event: Any, raw_event: dict[str, Any]) -> None: ...

    def record_outbound(self, **kwargs: Any) -> None: ...


@runtime_checkable
class RuntimeClientAPI(Protocol):
    contract_version: str

    def set_session(self, session_id: str) -> None: ...

    def get_run_status(self, run_id: str) -> Any: ...

    def list_runs(self, session_id: str | None = None) -> list[Any]: ...

    def cancel_run(self, run_id: str) -> bool: ...


@runtime_checkable
class AccessPolicyAPI(Protocol):
    contract_version: str

    def evaluate(self, inbound: Any, *, bot_username: str | None) -> Any: ...


@runtime_checkable
class IdentityAPI(Protocol):
    contract_version: str

    def resolve(self, *, channel: str, subject_id: str) -> str | None: ...

    def bind(
        self,
        *,
        principal_id: str,
        channel: str,
        subject_id: str,
        scopes: tuple[str, ...] | list[str] | None = None,
        status: str = PRINCIPAL_BINDING_STATUS_ACTIVE,
        note: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> None: ...


_REQUIRED_MEMBERS: dict[str, tuple[str, ...]] = {
    "session_store": (
        "resolve_session",
        "resolve_agent",
        "bind_session",
        "session_owner",
        "bind_session_owned",
        "ensure_agent",
        "persist_inbound",
        "attachment_refs_from_inputs",
        "append_turn",
        "set_pending_clarify",
        "get_pending_clarify",
        "clear_pending_clarify",
    ),
    "router": ("resolve",),
    "command_parser": ("parse",),
    "brain_client": ("run",),
    "outbound_sender": ("__call__",),
    "inbound_handler": ("handle_inbound",),
    "adapter": ("start",),
    "channel_adapter": ("start", "deliver"),
    "channel_registry": ("register", "get", "start_all", "stop_all", "health", "list"),
    "session_event_sink": ("record_inbound", "record_outbound"),
    "runtime_client": ("set_session", "get_run_status", "list_runs", "cancel_run"),
    "access_policy": ("evaluate",),
    "identity_api": ("resolve", "bind"),
}


def ensure_controlplane_component_compatibility(
    component: object, *, component_type: str
) -> None:
    if component_type not in _REQUIRED_MEMBERS:
        raise ValueError(f"unknown controlplane component_type '{component_type}'")
    for member in _REQUIRED_MEMBERS[component_type]:
        if not hasattr(component, member):
            raise TypeError(
                f"{component_type} missing required member '{member}' "
                f"(expected contract={CONTROLPLANE_INTERFACE_VERSION})"
            )
        member_value = getattr(component, member)
        if not callable(member_value):
            raise TypeError(
                f"{component_type}.{member} must be callable "
                f"(expected contract={CONTROLPLANE_INTERFACE_VERSION})"
            )
    version = getattr(component, "contract_version", None)
    if version != CONTROLPLANE_INTERFACE_VERSION:
        raise TypeError(
            f"{component_type} contract_version mismatch: got={version!r} "
            f"expected={CONTROLPLANE_INTERFACE_VERSION!r}"
        )
    if component_type == "channel_adapter":
        channel_id = str(getattr(component, "channel_id", "") or "").strip()
        if not channel_id:
            raise TypeError(
                f"{component_type} missing non-empty 'channel_id' "
                f"(expected contract={CONTROLPLANE_INTERFACE_VERSION})"
            )
