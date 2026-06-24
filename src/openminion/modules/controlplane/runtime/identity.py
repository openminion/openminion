from dataclasses import dataclass
from typing import Any

from ..constants import (
    DEFAULT_MINIMAL_SCOPES,
    PRINCIPAL_BINDING_STATUS_ACTIVE,
    PRINCIPAL_BINDING_STATUS_INACTIVE,
)
from ..interfaces import CONTROLPLANE_INTERFACE_VERSION
from ..contracts.models import AuthContext


@dataclass(frozen=True)
class PrincipalBinding:
    principal_id: str
    channel: str
    subject_id: str
    status: str
    scopes: tuple[str, ...]
    note: str | None
    metadata: dict[str, Any]


class StoreBackedIdentityAPI:
    """IdentityAPI surface backed by controlplane store principal tables."""

    contract_version = CONTROLPLANE_INTERFACE_VERSION

    def __init__(self, store: object) -> None:
        self._store = store

    def resolve(self, *, channel: str, subject_id: str) -> str | None:
        resolver = getattr(self._store, "resolve_principal", None)
        if not callable(resolver):
            raise TypeError("store missing resolve_principal(channel, subject_id)")
        value = resolver(channel=channel, subject_id=subject_id)
        if value is None:
            return None
        principal_id = str(value).strip()
        return principal_id or None

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
    ) -> None:
        binder = getattr(self._store, "bind_principal_subject", None)
        if not callable(binder):
            raise TypeError(
                "store missing bind_principal_subject(principal_id, channel, subject_id)"
            )
        binder(
            principal_id=principal_id,
            channel=channel,
            subject_id=subject_id,
            scopes=scopes,
            status=status,
            note=note,
            meta=meta,
        )

    def get_binding(self, *, channel: str, subject_id: str) -> PrincipalBinding | None:
        getter = getattr(self._store, "get_channel_subject", None)
        if not callable(getter):
            raise TypeError("store missing get_channel_subject(channel, subject_id)")
        record = getter(channel=channel, subject_id=subject_id)
        if not isinstance(record, dict):
            return None
        scopes_raw = record.get("scopes") or ()
        scopes = tuple(str(scope) for scope in scopes_raw if str(scope).strip())
        return PrincipalBinding(
            principal_id=str(record.get("principal_id") or ""),
            channel=str(record.get("channel") or channel),
            subject_id=str(record.get("subject_id") or subject_id),
            status=str(record.get("status") or PRINCIPAL_BINDING_STATUS_INACTIVE),
            scopes=scopes,
            note=str(record.get("note")) if record.get("note") is not None else None,
            metadata=dict(record.get("meta") or {}),
        )

    def auth_context(
        self,
        *,
        channel: str,
        subject_id: str,
        default_scopes: tuple[str, ...] = DEFAULT_MINIMAL_SCOPES,
    ) -> AuthContext | None:
        binding = self.get_binding(channel=channel, subject_id=subject_id)
        if binding is None or binding.status != PRINCIPAL_BINDING_STATUS_ACTIVE:
            return None
        scopes = binding.scopes or tuple(default_scopes)
        return AuthContext(
            role="paired",
            scopes=tuple(scopes),
            principal_id=binding.principal_id,
            metadata={
                "principal_id": binding.principal_id,
                "principal_binding": {
                    "channel": binding.channel,
                    "subject_id": binding.subject_id,
                    "status": binding.status,
                },
            },
        )
