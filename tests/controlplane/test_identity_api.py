from __future__ import annotations

from openminion.modules.controlplane.runtime.identity import StoreBackedIdentityAPI
from openminion.modules.controlplane.runtime.store import InMemoryControlPlaneStore


def test_identity_api_bind_and_resolve_roundtrip() -> None:
    store = InMemoryControlPlaneStore()
    principal_id = store.upsert_principal(principal_id="principal-42")
    api = StoreBackedIdentityAPI(store)

    api.bind(
        principal_id=principal_id,
        channel="telegram",
        subject_id="123",
        scopes=["cp.message.read", "cp.message.write"],
        status="active",
        meta={"topic_id": "88"},
    )

    resolved = api.resolve(channel="telegram", subject_id="123")
    assert resolved == principal_id


def test_identity_api_auth_context_uses_principal_id() -> None:
    store = InMemoryControlPlaneStore()
    principal_id = store.upsert_principal(principal_id="principal-room-9")
    api = StoreBackedIdentityAPI(store)
    api.bind(
        principal_id=principal_id,
        channel="telegram",
        subject_id="999",
        scopes=["cp.message.read"],
        status="active",
    )

    auth = api.auth_context(channel="telegram", subject_id="999")
    assert auth is not None
    assert auth.role == "paired"
    assert auth.principal_id == principal_id
    assert auth.metadata["principal_id"] == principal_id


def test_identity_api_auth_context_skips_non_active_binding() -> None:
    store = InMemoryControlPlaneStore()
    principal_id = store.upsert_principal(principal_id="principal-room-10")
    api = StoreBackedIdentityAPI(store)
    api.bind(
        principal_id=principal_id,
        channel="telegram",
        subject_id="1000",
        status="paused",
    )

    assert api.auth_context(channel="telegram", subject_id="1000") is None
