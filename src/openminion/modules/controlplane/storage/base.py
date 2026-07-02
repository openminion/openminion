from abc import ABC, abstractmethod
from typing import Any

from ..contracts.models import AttachmentInput, AttachmentRef, InboundMessage


class ControlplaneStore(ABC):
    """Abstract base for controlplane storage implementations."""

    # -- Chat bindings / sessions ------------------------------------------

    @abstractmethod
    def get_chat_binding(self, chat_key: str) -> dict[str, Any] | None: ...

    @abstractmethod
    def set_chat_binding(
        self, chat_key: str, session_id: str, agent_id: str | None = None
    ) -> None: ...

    @abstractmethod
    def resolve_session(self, user_key: str, chat_key: str) -> str: ...

    @abstractmethod
    def new_session(self, user_key: str, chat_key: str) -> str: ...

    @abstractmethod
    def rebind_session(self, user_key: str, chat_key: str) -> str: ...

    @abstractmethod
    def bind_session(self, user_key: str, chat_key: str, session_id: str) -> None: ...

    @abstractmethod
    def session_owner(self, session_id: str) -> str | None: ...

    @abstractmethod
    def bind_session_owned(
        self,
        *,
        user_key: str,
        chat_key: str,
        session_id: str,
        is_admin: bool,
    ) -> bool: ...

    @abstractmethod
    def list_sessions(
        self, user_key: str, chat_key: str | None = None
    ) -> list[dict[str, Any]]: ...

    @abstractmethod
    def set_session_title(self, session_id: str, title: str) -> None: ...

    @abstractmethod
    def get_session_title(self, session_id: str) -> str | None: ...

    @abstractmethod
    def list_session_bindings(self, limit: int = 1000) -> list[dict[str, Any]]: ...

    # -- Agents ------------------------------------------------------------

    @abstractmethod
    def set_agent(self, session_id: str, agent_id: str) -> None: ...

    @abstractmethod
    def resolve_agent(self, session_id: str) -> str: ...

    @abstractmethod
    def list_agents(self) -> list[dict[str, Any]]: ...

    @abstractmethod
    def ensure_agent(self, agent_id: str, name: str | None = None) -> None: ...

    # -- Users -------------------------------------------------------------

    @abstractmethod
    def get_user(self, user_key: str) -> dict[str, Any] | None: ...

    @abstractmethod
    def upsert_user(
        self,
        user_key: str,
        role: str = "user",
        profile_meta: dict[str, Any] | None = None,
    ) -> None: ...

    # -- Messages ----------------------------------------------------------

    @abstractmethod
    def put_inbound(
        self,
        *,
        chat_key: str,
        user_key: str,
        message_id: str,
        text: str | None,
        payload: dict[str, Any],
        session_id: str | None = None,
        agent_id: str | None = None,
    ) -> None: ...

    @abstractmethod
    def put_outbound(
        self,
        *,
        chat_key: str,
        text: str | None,
        payload: dict[str, Any],
        session_id: str | None = None,
        agent_id: str | None = None,
    ) -> None: ...

    @abstractmethod
    def persist_inbound(self, inbound: InboundMessage, session_id: str) -> None: ...

    @abstractmethod
    def append_turn(
        self,
        *,
        session_id: str,
        role: str,
        text: str | None,
        payload: dict[str, Any] | None = None,
        agent_id: str | None = None,
        chat_key: str | None = None,
        user_key: str | None = None,
        message_id: str | None = None,
    ) -> None: ...

    @abstractmethod
    def list_turns(self, session_id: str) -> list[dict[str, Any]]: ...

    @abstractmethod
    def attachment_refs_from_inputs(
        self, inputs: list[AttachmentInput | AttachmentRef]
    ) -> list[str]: ...

    # -- Pending clarify ---------------------------------------------------

    @abstractmethod
    def set_pending_clarify(self, session_id: str, payload: dict[str, Any]) -> None: ...

    @abstractmethod
    def get_pending_clarify(self, session_id: str) -> dict[str, Any] | None: ...

    @abstractmethod
    def clear_pending_clarify(self, session_id: str) -> None: ...

    @abstractmethod
    def list_pending_clarifies(self) -> list[dict[str, Any]]: ...

    # -- Inbox / outbox ----------------------------------------------------

    @abstractmethod
    def enqueue_inbox(
        self,
        *,
        channel: str,
        chat_id: str,
        channel_message_id: str,
        user_id: str,
        payload: dict[str, Any],
        thread_id: str | None = None,
        inbound_id: str | None = None,
    ) -> tuple[str, bool]: ...

    @abstractmethod
    def claim_inbox(
        self, *, lock_owner: str, reclaim_ttl_s: int = 120
    ) -> dict[str, Any] | None: ...

    @abstractmethod
    def ack_inbox(self, inbox_id: str) -> None: ...

    @abstractmethod
    def fail_inbox(self, inbox_id: str, error: str) -> None: ...

    @abstractmethod
    def enqueue_outbox(
        self,
        *,
        channel: str,
        chat_id: str,
        payload: dict[str, Any],
        thread_id: str | None = None,
        reply_to: str | None = None,
        outbox_id: str | None = None,
    ) -> str: ...

    @abstractmethod
    def claim_outbox(
        self, *, lock_owner: str, reclaim_ttl_s: int = 120
    ) -> dict[str, Any] | None: ...

    @abstractmethod
    def mark_outbox_sent(self, outbox_id: str) -> None: ...

    @abstractmethod
    def mark_outbox_retry(
        self,
        outbox_id: str,
        *,
        error: str,
        max_attempts: int = 8,
        max_backoff_s: int = 300,
    ) -> str: ...

    @abstractmethod
    def get_outbox(self, outbox_id: str) -> dict[str, Any] | None: ...

    # -- Pairings / principals ---------------------------------------------

    @abstractmethod
    def upsert_pairing(
        self,
        *,
        channel: str,
        chat_id: str,
        user_id: str,
        session_id: str,
        status: str = "active",
        scopes: list[str] | tuple[str, ...] | None = None,
        note: str | None = None,
        pairing_id: str | None = None,
    ) -> str: ...

    @abstractmethod
    def get_pairing(self, *, channel: str, chat_id: str) -> dict[str, Any] | None: ...

    @abstractmethod
    def list_pairings(
        self, *, channel: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]: ...

    @abstractmethod
    def touch_pairing(self, *, channel: str, chat_id: str) -> None: ...

    @abstractmethod
    def backfill_pairings_to_principals(
        self,
        *,
        channel: str | None = None,
        status: str = "active",
        limit: int | None = None,
    ) -> dict[str, int]: ...

    @abstractmethod
    def upsert_principal(
        self, *, principal_id: str | None = None, meta: dict[str, Any] | None = None
    ) -> str: ...

    @abstractmethod
    def bind_principal_subject(
        self,
        *,
        principal_id: str,
        channel: str,
        subject_id: str,
        status: str = "active",
        scopes: list[str] | tuple[str, ...] | None = None,
        note: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> None: ...

    @abstractmethod
    def resolve_principal(self, *, channel: str, subject_id: str) -> str | None: ...

    @abstractmethod
    def get_channel_subject(
        self, *, channel: str, subject_id: str
    ) -> dict[str, Any] | None: ...

    @abstractmethod
    def touch_channel_subject(self, *, channel: str, subject_id: str) -> None: ...

    # -- Rate limits -------------------------------------------------------

    @abstractmethod
    def increment_rate_limit(
        self, *, key_type: str, key_id: str, window_seconds: int, limit: int
    ) -> dict[str, Any]: ...

    # -- Audit -------------------------------------------------------------

    @abstractmethod
    def put_audit(self, event: Any) -> None: ...

    @abstractmethod
    def list_audit(
        self,
        *,
        event_type: str | None = None,
        session_id: str | None = None,
        trace_id: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]: ...

    # -- Lifecycle ---------------------------------------------------------

    @abstractmethod
    def close(self) -> None: ...
