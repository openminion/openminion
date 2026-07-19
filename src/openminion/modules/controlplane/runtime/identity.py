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


CHANNEL_IDENTITY_MEMORY_PROVENANCE_VERSION = "channel_identity_memory.v1"


@dataclass(frozen=True)
class ChannelIdentityProvenance:
    channel: str
    channel_account_id: str
    channel_thread_id: str
    participant_kind: str
    pairing_id: str | None
    namespace_id: str
    paired: bool
    schema_version: str = CHANNEL_IDENTITY_MEMORY_PROVENANCE_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "channel": self.channel,
            "channel_account_id": self.channel_account_id,
            "channel_thread_id": self.channel_thread_id,
            "participant_kind": self.participant_kind,
            "pairing_id": self.pairing_id,
            "namespace_id": self.namespace_id,
            "paired": self.paired,
        }


def paired_namespace_id(pairing_id: str) -> str:
    normalized = str(pairing_id or "").strip()
    if not normalized:
        raise ValueError("pairing_id is required")
    return f"memory-namespace:principal:{normalized}"


def isolated_channel_namespace_id(
    *,
    channel: str,
    channel_account_id: str,
    channel_thread_id: str,
    participant_kind: str,
) -> str:
    return ":".join(
        (
            "memory-namespace",
            "isolated-channel",
            str(channel or "").strip(),
            str(participant_kind or "").strip(),
            str(channel_account_id or "").strip(),
            str(channel_thread_id or "").strip(),
        )
    )


def resolve_channel_identity_memory_provenance(
    identity_api: "StoreBackedIdentityAPI",
    *,
    channel: str,
    channel_account_id: str,
    channel_thread_id: str,
    participant_kind: str,
) -> ChannelIdentityProvenance:
    subject_id = _subject_id(
        channel_account_id=channel_account_id,
        channel_thread_id=channel_thread_id,
        participant_kind=participant_kind,
    )
    binding = identity_api.get_binding(channel=channel, subject_id=subject_id)
    if binding is not None and binding.status == PRINCIPAL_BINDING_STATUS_ACTIVE:
        namespace_id = str(binding.metadata.get("namespace_id") or "").strip()
        return ChannelIdentityProvenance(
            channel=channel,
            channel_account_id=channel_account_id,
            channel_thread_id=channel_thread_id,
            participant_kind=participant_kind,
            pairing_id=binding.principal_id,
            namespace_id=namespace_id or paired_namespace_id(binding.principal_id),
            paired=True,
        )
    return ChannelIdentityProvenance(
        channel=channel,
        channel_account_id=channel_account_id,
        channel_thread_id=channel_thread_id,
        participant_kind=participant_kind,
        pairing_id=None,
        namespace_id=isolated_channel_namespace_id(
            channel=channel,
            channel_account_id=channel_account_id,
            channel_thread_id=channel_thread_id,
            participant_kind=participant_kind,
        ),
        paired=False,
    )


def bind_channel_identity_memory(
    identity_api: "StoreBackedIdentityAPI",
    *,
    pairing_id: str,
    channel: str,
    channel_account_id: str,
    channel_thread_id: str,
    participant_kind: str,
    namespace_id: str | None = None,
    scopes: tuple[str, ...] | list[str] | None = None,
) -> ChannelIdentityProvenance:
    subject_id = _subject_id(
        channel_account_id=channel_account_id,
        channel_thread_id=channel_thread_id,
        participant_kind=participant_kind,
    )
    resolved_namespace_id = namespace_id or paired_namespace_id(pairing_id)
    identity_api.bind(
        principal_id=pairing_id,
        channel=channel,
        subject_id=subject_id,
        scopes=scopes,
        status=PRINCIPAL_BINDING_STATUS_ACTIVE,
        meta={
            "namespace_id": resolved_namespace_id,
            "channel_account_id": channel_account_id,
            "channel_thread_id": channel_thread_id,
            "participant_kind": participant_kind,
            "schema_version": CHANNEL_IDENTITY_MEMORY_PROVENANCE_VERSION,
        },
    )
    return resolve_channel_identity_memory_provenance(
        identity_api,
        channel=channel,
        channel_account_id=channel_account_id,
        channel_thread_id=channel_thread_id,
        participant_kind=participant_kind,
    )


def unpair_channel_identity_memory(
    identity_api: "StoreBackedIdentityAPI",
    *,
    channel: str,
    channel_account_id: str,
    channel_thread_id: str,
    participant_kind: str,
) -> None:
    subject_id = _subject_id(
        channel_account_id=channel_account_id,
        channel_thread_id=channel_thread_id,
        participant_kind=participant_kind,
    )
    binding = identity_api.get_binding(channel=channel, subject_id=subject_id)
    if binding is None:
        return
    identity_api.bind(
        principal_id=binding.principal_id,
        channel=channel,
        subject_id=subject_id,
        scopes=binding.scopes,
        status=PRINCIPAL_BINDING_STATUS_INACTIVE,
        note=binding.note,
        meta=binding.metadata,
    )


def _subject_id(
    *,
    channel_account_id: str,
    channel_thread_id: str,
    participant_kind: str,
) -> str:
    return (
        f"{str(participant_kind or '').strip()}:"
        f"{str(channel_account_id or '').strip()}:"
        f"{str(channel_thread_id or '').strip()}"
    )


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
