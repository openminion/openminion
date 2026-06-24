import re
import uuid
from typing import Any
from typing import Protocol

from ..contracts.inbound import inbound_metadata
from ..interfaces import CONTROLPLANE_INTERFACE_VERSION
from ..contracts.models import InboundMessage, ResolvedContext
from .audit import emit_audit_event
from .store import InMemoryControlPlaneStore


class SessionResolver(Protocol):
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


_AGENT_MENTION_RE = re.compile(r"@([A-Za-z0-9:_-]+)")
_SESSION_RESERVED = {
    "new",
    "use",
    "title",
    "id",
    "status",
    "list",
    "export",
    "sessions",
}


class Router:
    """Deterministically maps inbound identities to session + agent."""

    contract_version = CONTROLPLANE_INTERFACE_VERSION

    def __init__(
        self,
        store: SessionResolver | InMemoryControlPlaneStore,
        *,
        auth: Any | None = None,
        audit_logger: Any | None = None,
    ) -> None:
        self.store = store
        self.auth = auth
        self.audit_logger = audit_logger

    def resolve(self, inbound: InboundMessage) -> ResolvedContext:
        session_override = self._extract_session_override(inbound.text)
        if session_override:
            is_admin = bool(
                self.auth is not None
                and hasattr(self.auth, "is_admin")
                and self.auth.is_admin(inbound.user_key)
            )
            owner = (
                self.store.session_owner(session_override)
                if hasattr(self.store, "session_owner")
                else None
            )
            if hasattr(
                self.store, "bind_session_owned"
            ) and self.store.bind_session_owned(
                user_key=inbound.user_key,
                chat_key=inbound.chat_key,
                session_id=session_override,
                is_admin=is_admin,
            ):
                if owner is not None and owner != inbound.user_key and is_admin:
                    self._emit_audit(
                        "session.bind.admin_override",
                        user_key=inbound.user_key,
                        chat_key=inbound.chat_key,
                        requested_session_id=session_override,
                        owner_user_key=owner,
                    )
                session_id = session_override
            else:
                reason = "missing_session" if owner is None else "owner_mismatch"
                self._emit_audit(
                    "session.bind.denied",
                    user_key=inbound.user_key,
                    chat_key=inbound.chat_key,
                    requested_session_id=session_override,
                    owner_user_key=owner,
                    reason=reason,
                )
                session_id = self.store.resolve_session(
                    inbound.user_key, inbound.chat_key
                )
        else:
            session_id = self.store.resolve_session(inbound.user_key, inbound.chat_key)

        agent_id = self._resolve_agent(inbound.text, session_id)
        trace_id = self._extract_trace_id(inbound) or uuid.uuid4().hex
        span_id = uuid.uuid4().hex[:16]
        return ResolvedContext(
            user_key=inbound.user_key,
            chat_key=inbound.chat_key,
            session_id=session_id,
            agent_id=agent_id,
            role="user",
            trace_id=trace_id,
            span_id=span_id,
        )

    def _extract_trace_id(self, inbound: InboundMessage) -> str | None:
        meta = inbound_metadata(inbound)
        direct = str(meta.get("trace_id", "")).strip()
        if direct:
            return direct
        cp_trace = str(meta.get("controlplane.trace_id", "")).strip()
        if cp_trace:
            return cp_trace
        nested = meta.get("control_event")
        if isinstance(nested, dict):
            nested_trace = str(nested.get("trace_id", "")).strip()
            if nested_trace:
                return nested_trace
        return None

    def _extract_session_override(self, text: str) -> str | None:
        stripped = (text or "").strip()
        if not stripped.startswith("/session "):
            return None
        parts = stripped.split()
        if len(parts) < 2:
            return None
        candidate = parts[1].strip()
        if not candidate or candidate.lower() in _SESSION_RESERVED:
            return None
        return candidate

    def _resolve_agent(self, text: str, session_id: str) -> str:
        mention = _AGENT_MENTION_RE.search(text or "")
        if mention:
            agent_name = mention.group(1)
            if hasattr(self.store, "ensure_agent"):
                self.store.ensure_agent(agent_name, name=agent_name)
            return agent_name
        return self.store.resolve_agent(session_id)

    def _emit_audit(self, event_type: str, **details: object) -> None:
        emit_audit_event(self.audit_logger, event_type, **details)
