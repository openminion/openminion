from __future__ import annotations

import pytest

from openminion.modules.controlplane.commands.registry import CommandRegistry
from openminion.modules.controlplane.contracts.models import (
    InboundMessage,
    ParsedCommand,
    ResolvedContext,
)
from openminion.modules.controlplane.runtime.audit import AuditLogger
from openminion.modules.controlplane.runtime.auth import AuthEvaluator
from openminion.modules.controlplane.runtime.router import Router
from openminion.modules.controlplane.runtime.store import InMemoryControlPlaneStore


def _ctx(user_key: str, chat_key: str, session_id: str) -> ResolvedContext:
    return ResolvedContext(
        user_key=user_key,
        chat_key=chat_key,
        session_id=session_id,
        agent_id="agent:default",
        role="user",
        trace_id="trace-1",
        span_id="span-1",
    )


def _latest_event(audit: AuditLogger, event_type: str) -> dict[str, object]:
    events = [
        event.to_dict() for event in audit.events if event.event_type == event_type
    ]
    assert events, f"missing audit event {event_type}"
    return events[-1]


@pytest.mark.parametrize("call_site", ["router", "command"])
def test_session_bind_owned_allows_owner_match(call_site: str) -> None:
    store = InMemoryControlPlaneStore()
    audit = AuditLogger()
    auth = AuthEvaluator(admin_user_keys=["user:admin"])
    alice_session = store.resolve_session("user:alice", "chat:alice")

    if call_site == "router":
        router = Router(store, auth=auth, audit_logger=audit)
        ctx = router.resolve(
            InboundMessage(
                user_key="user:alice",
                chat_key="chat:alice",
                text=f"/session {alice_session}",
            )
        )
        assert ctx.session_id == alice_session
    else:
        registry = CommandRegistry(store=store, auth=auth, audit_logger=audit)
        result = registry.execute(
            ParsedCommand(
                canonical="session.use",
                original_text=f"/session use {alice_session}",
                args=[alice_session],
            ),
            _ctx("user:alice", "chat:alice", alice_session),
        )
        assert result.ok is True
        assert result.data["session_id"] == alice_session

    assert [
        event for event in audit.events if event.event_type.startswith("session.bind")
    ] == []


@pytest.mark.parametrize("call_site", ["router", "command"])
def test_session_bind_owned_denies_cross_owner_non_admin(call_site: str) -> None:
    store = InMemoryControlPlaneStore()
    audit = AuditLogger()
    auth = AuthEvaluator(admin_user_keys=["user:admin"])
    alice_session = store.resolve_session("user:alice", "chat:alice")
    bob_session = store.resolve_session("user:bob", "chat:bob")

    if call_site == "router":
        router = Router(store, auth=auth, audit_logger=audit)
        ctx = router.resolve(
            InboundMessage(
                user_key="user:bob",
                chat_key="chat:bob",
                text=f"/session {alice_session}",
            )
        )
        assert ctx.session_id == bob_session
        assert store.resolve_session("user:bob", "chat:bob") == bob_session
    else:
        registry = CommandRegistry(store=store, auth=auth, audit_logger=audit)
        result = registry.execute(
            ParsedCommand(
                canonical="session.use",
                original_text=f"/session use {alice_session}",
                args=[alice_session],
            ),
            _ctx("user:bob", "chat:bob", bob_session),
        )
        assert result.ok is False
        assert result.error == {
            "code": "SESSION_BIND_DENIED",
            "reason": "owner_mismatch",
        }
        assert store.resolve_session("user:bob", "chat:bob") == bob_session

    denied = _latest_event(audit, "session.bind.denied")
    assert denied["details"]["requested_session_id"] == alice_session
    assert denied["details"]["owner_user_key"] == "user:alice"
    assert denied["details"]["reason"] == "owner_mismatch"


@pytest.mark.parametrize("call_site", ["router", "command"])
def test_session_bind_owned_allows_admin_override(call_site: str) -> None:
    store = InMemoryControlPlaneStore()
    audit = AuditLogger()
    auth = AuthEvaluator(admin_user_keys=["user:admin"])
    alice_session = store.resolve_session("user:alice", "chat:alice")
    admin_session = store.resolve_session("user:admin", "chat:admin")

    if call_site == "router":
        router = Router(store, auth=auth, audit_logger=audit)
        ctx = router.resolve(
            InboundMessage(
                user_key="user:admin",
                chat_key="chat:admin",
                text=f"/session {alice_session}",
            )
        )
        assert ctx.session_id == alice_session
    else:
        registry = CommandRegistry(store=store, auth=auth, audit_logger=audit)
        result = registry.execute(
            ParsedCommand(
                canonical="session.use",
                original_text=f"/session use {alice_session}",
                args=[alice_session],
            ),
            _ctx("user:admin", "chat:admin", admin_session),
        )
        assert result.ok is True
        assert result.data["session_id"] == alice_session

    assert store.resolve_session("user:admin", "chat:admin") == alice_session
    override = _latest_event(audit, "session.bind.admin_override")
    assert override["details"]["requested_session_id"] == alice_session
    assert override["details"]["owner_user_key"] == "user:alice"


@pytest.mark.parametrize("call_site", ["router", "command"])
def test_session_bind_owned_denies_missing_session(call_site: str) -> None:
    store = InMemoryControlPlaneStore()
    audit = AuditLogger()
    auth = AuthEvaluator(admin_user_keys=["user:admin"])
    bob_session = store.resolve_session("user:bob", "chat:bob")
    missing_session = "sess-missing"

    if call_site == "router":
        router = Router(store, auth=auth, audit_logger=audit)
        ctx = router.resolve(
            InboundMessage(
                user_key="user:bob",
                chat_key="chat:bob",
                text=f"/session {missing_session}",
            )
        )
        assert ctx.session_id == bob_session
    else:
        registry = CommandRegistry(store=store, auth=auth, audit_logger=audit)
        result = registry.execute(
            ParsedCommand(
                canonical="session.use",
                original_text=f"/session use {missing_session}",
                args=[missing_session],
            ),
            _ctx("user:bob", "chat:bob", bob_session),
        )
        assert result.ok is False
        assert result.error == {
            "code": "SESSION_BIND_DENIED",
            "reason": "missing_session",
        }

    denied = _latest_event(audit, "session.bind.denied")
    assert denied["details"]["requested_session_id"] == missing_session
    assert denied["details"]["reason"] == "missing_session"
