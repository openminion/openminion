"""Optional session encryption helpers with key-ring rotation support."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from cryptography.fernet import Fernet, InvalidToken

from openminion.modules.session.interfaces import (
    SESSION_ENCRYPTION_MIGRATION_VERSION,
    SESSION_ENCRYPTION_SCHEMA_VERSION,
)


class SessionEncryptionError(RuntimeError):
    code = "SESSION_ENCRYPTION_ERROR"


class SessionEncryptionKeyError(SessionEncryptionError):
    code = "SESSION_ENCRYPTION_KEY_UNAVAILABLE"


class SessionEncryptionIdentityError(SessionEncryptionError):
    code = "SESSION_ENCRYPTION_RECORD_IDENTITY_MISMATCH"


class SessionContentSearchDisabledError(SessionEncryptionError):
    code = "SESSION_CONTENT_SEARCH_DISABLED"


@dataclass(frozen=True)
class SessionEncryptionEnvelope:
    schema_version: str
    key_id: str
    purpose: str
    record_identity: dict[str, str]
    ciphertext: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "key_id": self.key_id,
            "purpose": self.purpose,
            "record_identity": dict(self.record_identity),
            "ciphertext": self.ciphertext,
        }


class SessionKeyRing(Protocol):
    @property
    def active_key_id(self) -> str: ...

    def encrypt(self, *, plaintext: bytes, purpose: str, record_identity: Mapping[str, str]) -> SessionEncryptionEnvelope: ...

    def decrypt(self, envelope: SessionEncryptionEnvelope) -> bytes: ...

    def rotate(self, *, key_id: str, key: bytes | str | None = None) -> None: ...

    def can_remove_key(self, key_id: str, referenced_key_ids: set[str]) -> bool: ...


class FernetSessionKeyRing:
    def __init__(self, *, active_key_id: str = "k1", keys: Mapping[str, bytes | str] | None = None) -> None:
        initial = dict(keys or {active_key_id: Fernet.generate_key()})
        if active_key_id not in initial:
            initial[active_key_id] = Fernet.generate_key()
        self._keys = {key_id: _normalize_key(key) for key_id, key in initial.items()}
        self._active_key_id = active_key_id

    @property
    def active_key_id(self) -> str:
        return self._active_key_id

    def encrypt(
        self,
        *,
        plaintext: bytes,
        purpose: str,
        record_identity: Mapping[str, str],
    ) -> SessionEncryptionEnvelope:
        identity = {str(key): str(value) for key, value in record_identity.items()}
        payload = json.dumps(
            {"purpose": purpose, "record_identity": identity, "plaintext": base64.b64encode(plaintext).decode("ascii")},
            ensure_ascii=True,
            sort_keys=True,
        ).encode("utf-8")
        token = Fernet(self._keys[self._active_key_id]).encrypt(payload).decode("ascii")
        return SessionEncryptionEnvelope(
            schema_version=SESSION_ENCRYPTION_SCHEMA_VERSION,
            key_id=self._active_key_id,
            purpose=purpose,
            record_identity=identity,
            ciphertext=token,
        )

    def decrypt(self, envelope: SessionEncryptionEnvelope) -> bytes:
        key = self._keys.get(envelope.key_id)
        if key is None:
            raise SessionEncryptionKeyError(f"missing session encryption key: {envelope.key_id}")
        try:
            raw = Fernet(key).decrypt(envelope.ciphertext.encode("ascii"))
            payload = json.loads(raw.decode("utf-8"))
        except (InvalidToken, ValueError, json.JSONDecodeError) as exc:
            raise SessionEncryptionKeyError("session ciphertext could not be decrypted") from exc
        if payload.get("purpose") != envelope.purpose:
            raise SessionEncryptionIdentityError("session ciphertext purpose mismatch")
        if dict(payload.get("record_identity") or {}) != envelope.record_identity:
            raise SessionEncryptionIdentityError("session ciphertext identity mismatch")
        return base64.b64decode(str(payload.get("plaintext") or ""))

    def rotate(self, *, key_id: str, key: bytes | str | None = None) -> None:
        normalized = str(key_id or "").strip()
        if not normalized:
            raise ValueError("key_id is required")
        self._keys[normalized] = _normalize_key(key or Fernet.generate_key())
        self._active_key_id = normalized

    def can_remove_key(self, key_id: str, referenced_key_ids: set[str]) -> bool:
        return str(key_id) not in {str(item) for item in referenced_key_ids}


@dataclass(frozen=True)
class SessionEncryptionMigrationCheckpoint:
    checkpoint_id: str
    migrated_count: int
    last_record_id: str | None
    complete: bool
    schema_version: str = SESSION_ENCRYPTION_MIGRATION_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "checkpoint_id": self.checkpoint_id,
            "migrated_count": self.migrated_count,
            "last_record_id": self.last_record_id,
            "complete": self.complete,
        }


def encrypt_session_payload(
    key_ring: SessionKeyRing,
    *,
    payload: Mapping[str, Any],
    purpose: str,
    record_identity: Mapping[str, str],
) -> dict[str, Any]:
    encoded = json.dumps(dict(payload), ensure_ascii=True, sort_keys=True).encode("utf-8")
    return key_ring.encrypt(
        plaintext=encoded,
        purpose=purpose,
        record_identity=record_identity,
    ).to_dict()


def decrypt_session_payload(
    key_ring: SessionKeyRing,
    envelope_payload: Mapping[str, Any],
    *,
    expected_identity: Mapping[str, str],
) -> dict[str, Any]:
    envelope = _envelope_from_mapping(envelope_payload)
    if envelope.record_identity != {str(k): str(v) for k, v in expected_identity.items()}:
        raise SessionEncryptionIdentityError("session ciphertext identity mismatch")
    decoded = key_ring.decrypt(envelope).decode("utf-8")
    value = json.loads(decoded)
    return dict(value) if isinstance(value, dict) else {"value": value}


def assert_content_search_allowed(*, encryption_enabled: bool) -> None:
    if encryption_enabled:
        raise SessionContentSearchDisabledError("content search is disabled for encrypted sessions")


def referenced_key_ids(envelopes: list[Mapping[str, Any]]) -> set[str]:
    return {str(item.get("key_id")) for item in envelopes if item.get("key_id")}


def build_migration_checkpoint(
    *,
    checkpoint_id: str,
    migrated_count: int,
    last_record_id: str | None,
    complete: bool,
) -> SessionEncryptionMigrationCheckpoint:
    return SessionEncryptionMigrationCheckpoint(
        checkpoint_id=checkpoint_id,
        migrated_count=max(0, int(migrated_count)),
        last_record_id=last_record_id,
        complete=bool(complete),
    )


def _envelope_from_mapping(value: Mapping[str, Any]) -> SessionEncryptionEnvelope:
    return SessionEncryptionEnvelope(
        schema_version=str(value.get("schema_version") or SESSION_ENCRYPTION_SCHEMA_VERSION),
        key_id=str(value.get("key_id") or ""),
        purpose=str(value.get("purpose") or ""),
        record_identity={str(k): str(v) for k, v in dict(value.get("record_identity") or {}).items()},
        ciphertext=str(value.get("ciphertext") or ""),
    )


def _normalize_key(key: bytes | str) -> bytes:
    return key.encode("ascii") if isinstance(key, str) else key
