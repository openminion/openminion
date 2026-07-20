from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from openminion.modules.controlplane.config import ControlPlaneConfig
from openminion.modules.controlplane.constants import (
    PRINCIPAL_BINDING_STATUS_INACTIVE,
)
from openminion.modules.controlplane.runtime.audit import AuditLogger
from openminion.modules.controlplane.storage.sqlite import SQLiteControlPlaneStore

_PAIRING_FIELDS = (
    "principal_id",
    "channel",
    "subject_id",
    "status",
    "scopes",
    "note",
    "created_at",
    "last_seen_at",
)


@dataclass(frozen=True)
class PairingAdminResult:
    found: bool
    pairing: dict[str, Any] | None = None


class ControlPlanePairingAdmin:
    """Local-owner administration for channel/principal bindings."""

    def __init__(self, store: Any) -> None:
        self._store = store
        self._audit = AuditLogger(sink=store.put_audit)

    def list_pairings(
        self,
        *,
        channel: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        rows = self._store.list_channel_subjects(
            channel=channel,
            status=status,
            limit=limit,
        )
        return [_public_pairing(row) for row in rows]

    def show_pairing(
        self, *, channel: str, subject_id: str
    ) -> PairingAdminResult:
        row = self._store.get_channel_subject(
            channel=channel,
            subject_id=subject_id,
        )
        if row is None:
            return PairingAdminResult(found=False)
        return PairingAdminResult(found=True, pairing=_public_pairing(row))

    def set_scopes(
        self,
        *,
        channel: str,
        subject_id: str,
        scopes: list[str],
    ) -> PairingAdminResult:
        existing = self._store.get_channel_subject(
            channel=channel,
            subject_id=subject_id,
        )
        if existing is None:
            return PairingAdminResult(found=False)
        self._store.update_channel_subject(
            channel=channel,
            subject_id=subject_id,
            scopes=scopes,
        )
        self._emit(
            "cp.pairing.binding.scopes_updated",
            channel=channel,
            subject_id=subject_id,
            principal_id=str(existing.get("principal_id") or ""),
            scopes=scopes,
        )
        return self.show_pairing(channel=channel, subject_id=subject_id)

    def revoke(
        self,
        *,
        channel: str,
        subject_id: str,
        note: str | None = None,
    ) -> PairingAdminResult:
        existing = self._store.get_channel_subject(
            channel=channel,
            subject_id=subject_id,
        )
        if existing is None:
            return PairingAdminResult(found=False)
        self._store.update_channel_subject(
            channel=channel,
            subject_id=subject_id,
            status=PRINCIPAL_BINDING_STATUS_INACTIVE,
            note=note,
        )
        self._emit(
            "cp.pairing.binding.revoked",
            channel=channel,
            subject_id=subject_id,
            principal_id=str(existing.get("principal_id") or ""),
        )
        return self.show_pairing(channel=channel, subject_id=subject_id)

    def close(self) -> None:
        self._store.close()

    def _emit(
        self,
        event_type: str,
        *,
        channel: str,
        subject_id: str,
        principal_id: str,
        scopes: list[str] | None = None,
    ) -> None:
        details: dict[str, Any] = {
            "channel": channel,
            "subject_id": subject_id,
            "principal_id": principal_id,
        }
        if scopes is not None:
            details["scopes"] = list(scopes)
        self._audit.emit(event_type, details=details)


def open_pairing_admin(config: ControlPlaneConfig) -> ControlPlanePairingAdmin:
    return ControlPlanePairingAdmin(
        SQLiteControlPlaneStore(config.sqlite_path, wal=config.wal)
    )


def _public_pairing(row: dict[str, Any] | None) -> dict[str, Any]:
    safe = dict(row or {})
    if "scopes" not in safe:
        safe["scopes"] = []
    return {field: safe.get(field) for field in _PAIRING_FIELDS}
