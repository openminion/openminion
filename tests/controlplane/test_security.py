from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from openminion.modules.controlplane.contracts.models import InboundMessage
from openminion.modules.controlplane.runtime.security import ScopeAuthorizer


@dataclass
class _StubStore:
    looked_up: list[tuple[str, str]]

    def get_pairing(self, *, channel: str, chat_id: str) -> dict[str, Any] | None:
        self.looked_up.append((channel, chat_id))
        if chat_id == "100":
            return {
                "pairing_id": "pair-1",
                "scopes": ["cp.message.read", "cp.message.write"],
            }
        return None


@dataclass
class _PrincipalFirstStore:
    principal_lookups: list[tuple[str, str]]
    subject_lookups: list[tuple[str, str]]
    pairing_lookups: list[tuple[str, str]]
    touched_subjects: list[tuple[str, str]]

    def resolve_principal(self, *, channel: str, subject_id: str) -> str | None:
        self.principal_lookups.append((channel, subject_id))
        if subject_id == "100":
            return "principal-room-100"
        return None

    def get_channel_subject(
        self, *, channel: str, subject_id: str
    ) -> dict[str, Any] | None:
        self.subject_lookups.append((channel, subject_id))
        if subject_id == "100":
            return {
                "principal_id": "principal-room-100",
                "status": "active",
                "scopes": ["cp.message.read", "cp.message.write"],
            }
        return None

    def get_pairing(self, *, channel: str, chat_id: str) -> dict[str, Any] | None:
        self.pairing_lookups.append((channel, chat_id))
        return {
            "pairing_id": "pair-legacy-100",
            "scopes": ["legacy.scope"],
        }

    def touch_channel_subject(self, *, channel: str, subject_id: str) -> None:
        self.touched_subjects.append((channel, subject_id))


def test_scope_authorizer_prefers_canonical_chat_id_for_lookup() -> None:
    store = _StubStore(looked_up=[])
    inbound = InboundMessage(
        user_key="telegram:42",
        chat_key="telegram:100",
        text="/help",
        channel="telegram",
        chat_id="100",
        user_id="42",
    )

    auth = ScopeAuthorizer(store=store).auth_for_inbound(inbound)

    assert store.looked_up == [("telegram", "100")]
    assert auth.role == "paired"


def test_scope_authorizer_prefers_principal_mapping_before_pairing_lookup() -> None:
    store = _PrincipalFirstStore(
        principal_lookups=[],
        subject_lookups=[],
        pairing_lookups=[],
        touched_subjects=[],
    )
    inbound = InboundMessage(
        user_key="telegram:42",
        chat_key="telegram:100",
        text="/help",
        channel="telegram",
        chat_id="100",
        user_id="42",
    )

    auth = ScopeAuthorizer(store=store).auth_for_inbound(inbound)

    assert store.principal_lookups == [("telegram", "100")]
    assert store.subject_lookups == [("telegram", "100")]
    assert store.pairing_lookups == []
    assert store.touched_subjects == [("telegram", "100")]
    assert auth.role == "paired"
    assert auth.principal_id == "principal-room-100"
    assert auth.metadata["principal_id"] == "principal-room-100"


def test_scope_authorizer_falls_back_to_pairing_lookup_when_principal_missing() -> None:
    store = _PrincipalFirstStore(
        principal_lookups=[],
        subject_lookups=[],
        pairing_lookups=[],
        touched_subjects=[],
    )
    inbound = InboundMessage(
        user_key="telegram:42",
        chat_key="telegram:101",
        text="/help",
        channel="telegram",
        chat_id="101",
        user_id="42",
    )

    auth = ScopeAuthorizer(store=store).auth_for_inbound(inbound)

    assert store.principal_lookups == [("telegram", "101")]
    assert store.pairing_lookups == [("telegram", "101")]
    assert auth.role == "paired"
    assert auth.principal_id == "pair-legacy-100"
