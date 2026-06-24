from typing import Any, Protocol, runtime_checkable

TELEGRAM_INTERFACE_VERSION = "v1"


@runtime_checkable
class RuntimeHandlerAPI(Protocol):
    contract_version: str

    def handle_inbound(self, inbound: Any) -> dict[str, Any]: ...


@runtime_checkable
class BotAPI(Protocol):
    contract_version: str

    def get_me(self) -> dict[str, Any]: ...

    def delete_webhook(
        self, *, drop_pending_updates: bool = False
    ) -> dict[str, Any]: ...

    def get_updates(
        self,
        *,
        offset: int | None,
        timeout: int,
        limit: int,
        allowed_updates: list[str],
    ) -> list[dict[str, Any]]: ...

    def send_message(self, payload: dict[str, Any]) -> dict[str, Any]: ...

    def edit_message_text(self, payload: dict[str, Any]) -> dict[str, Any]: ...

    def answer_callback_query(self, callback_query_id: str) -> dict[str, Any]: ...


@runtime_checkable
class DeliveryServiceAPI(Protocol):
    contract_version: str

    def send_payload(self, payload: dict[str, Any], target: Any) -> Any: ...

    def send_text(self, *, text: str, target: Any) -> Any: ...


@runtime_checkable
class StateStoreAPI(Protocol):
    contract_version: str

    def get_last_update_id(self, account_id: str) -> int: ...

    def set_last_update_id(self, account_id: str, update_id: int) -> None: ...

    def issue_pair_token(self, **kwargs: Any) -> Any: ...

    def consume_pair_token(self, **kwargs: Any) -> Any: ...

    def count_recent_attempts_for_user(
        self, *, user_id: int, window_seconds: int
    ) -> int: ...

    def count_recent_attempts_for_chat(
        self, *, chat_id: int, window_seconds: int
    ) -> int: ...

    def record_pair_attempt(self, **kwargs: Any) -> None: ...


@runtime_checkable
class SessionSinkAPI(Protocol):
    contract_version: str

    def record_inbound(self, event: Any, raw_update: dict[str, Any]) -> None: ...

    def record_outbound(self, **kwargs: Any) -> None: ...


@runtime_checkable
class PairingServiceAPI(Protocol):
    contract_version: str

    def handle_start_pairing(
        self, envelope: Any, *, bot_username: str | None
    ) -> Any: ...


_REQUIRED_MEMBERS: dict[str, tuple[str, ...]] = {
    "runtime_handler": ("handle_inbound",),
    "bot_api": (
        "get_me",
        "delete_webhook",
        "get_updates",
        "send_message",
        "edit_message_text",
        "answer_callback_query",
    ),
    "delivery_service": ("send_payload", "send_text"),
    "state_store": (
        "get_last_update_id",
        "set_last_update_id",
        "issue_pair_token",
        "consume_pair_token",
        "count_recent_attempts_for_user",
        "count_recent_attempts_for_chat",
        "record_pair_attempt",
    ),
    "session_sink": ("record_inbound", "record_outbound"),
    "pairing_service": ("handle_start_pairing",),
}


def ensure_telegram_component_compatibility(
    component: object, *, component_type: str
) -> None:
    if component_type not in _REQUIRED_MEMBERS:
        raise ValueError(f"unknown telegram component_type '{component_type}'")
    for member in _REQUIRED_MEMBERS[component_type]:
        if not hasattr(component, member):
            raise TypeError(
                f"{component_type} missing required member '{member}' "
                f"(expected contract={TELEGRAM_INTERFACE_VERSION})"
            )
        member_value = getattr(component, member)
        if not callable(member_value):
            raise TypeError(
                f"{component_type}.{member} must be callable "
                f"(expected contract={TELEGRAM_INTERFACE_VERSION})"
            )
    version = getattr(component, "contract_version", None)
    if version != TELEGRAM_INTERFACE_VERSION:
        raise TypeError(
            f"{component_type} contract_version mismatch: got={version!r} "
            f"expected={TELEGRAM_INTERFACE_VERSION!r}"
        )
