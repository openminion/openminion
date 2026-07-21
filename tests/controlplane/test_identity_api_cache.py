from __future__ import annotations

from openminion.modules.controlplane.runtime.identity import (
    CachedIdentityAPI,
    StoreBackedIdentityAPI,
)


class _PrincipalStore:
    def __init__(self) -> None:
        self.bindings: dict[tuple[str, str], dict[str, object]] = {}
        self.resolve_calls = 0

    def resolve_principal(self, *, channel: str, subject_id: str) -> str | None:
        self.resolve_calls += 1
        record = self.bindings.get((channel, subject_id))
        if record is None:
            return None
        return str(record["principal_id"])

    def bind_principal_subject(
        self,
        *,
        principal_id: str,
        channel: str,
        subject_id: str,
        scopes: tuple[str, ...] | list[str] | None = None,
        status: str = "active",
        note: str | None = None,
        meta: dict[str, object] | None = None,
    ) -> None:
        self.bindings[(channel, subject_id)] = {
            "principal_id": principal_id,
            "channel": channel,
            "subject_id": subject_id,
            "status": status,
            "scopes": tuple(scopes or ()),
            "note": note,
            "meta": dict(meta or {}),
        }

    def get_channel_subject(
        self, *, channel: str, subject_id: str
    ) -> dict[str, object] | None:
        return self.bindings.get((channel, subject_id))


def test_cached_identity_api_caches_positive_resolve_until_expiry() -> None:
    now = 1000.0

    def clock() -> float:
        return now

    store = _PrincipalStore()
    inner = StoreBackedIdentityAPI(store)
    inner.bind(principal_id="p1", channel="telegram", subject_id="chat:1")
    cached = CachedIdentityAPI(inner, maxsize=10, ttl_seconds=5, clock=clock)

    assert cached.resolve(channel="telegram", subject_id="chat:1") == "p1"
    assert cached.resolve(channel="telegram", subject_id="chat:1") == "p1"
    assert store.resolve_calls == 1

    now = 1006.0
    assert cached.resolve(channel="telegram", subject_id="chat:1") == "p1"
    assert store.resolve_calls == 2


def test_cached_identity_api_invalidates_exact_key_on_bind() -> None:
    store = _PrincipalStore()
    inner = StoreBackedIdentityAPI(store)
    cached = CachedIdentityAPI(inner, maxsize=10, ttl_seconds=300)

    cached.bind(principal_id="p1", channel="telegram", subject_id="chat:1")
    cached.bind(principal_id="p2", channel="slack", subject_id="chat:1")
    assert cached.resolve(channel="telegram", subject_id="chat:1") == "p1"
    assert cached.resolve(channel="slack", subject_id="chat:1") == "p2"
    assert store.resolve_calls == 2

    cached.bind(principal_id="p3", channel="telegram", subject_id="chat:1")
    assert cached.resolve(channel="telegram", subject_id="chat:1") == "p3"
    assert cached.resolve(channel="slack", subject_id="chat:1") == "p2"
    assert store.resolve_calls == 3


def test_cached_identity_api_does_not_cache_negative_results() -> None:
    store = _PrincipalStore()
    inner = StoreBackedIdentityAPI(store)
    cached = CachedIdentityAPI(inner, maxsize=10, ttl_seconds=300)

    assert cached.resolve(channel="telegram", subject_id="chat:1") is None
    assert cached.resolve(channel="telegram", subject_id="chat:1") is None
    assert store.resolve_calls == 2

    cached.bind(principal_id="p1", channel="telegram", subject_id="chat:1")
    assert cached.resolve(channel="telegram", subject_id="chat:1") == "p1"
