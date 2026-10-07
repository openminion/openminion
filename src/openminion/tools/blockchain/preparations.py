from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Callable

from openminion.base.config import resolve_data_root, resolve_home_root
from openminion.base.config.env import EnvironmentConfig, resolve_environment_config

from .transaction_schemas import (
    RESOLVED_PREPARATION_ADAPTER,
    SEND_REQUEST_ADAPTER,
    resolved_preparation_digest,
    validate_resolved_preparation_record,
)


class PreparationReferenceError(ValueError):
    pass


class SessionRecordError(ValueError):
    def __init__(self, message: str, *, reason: str = "invalid") -> None:
        super().__init__(message)
        self.reason = reason


RecordValidator = Callable[[Mapping[str, Any]], Mapping[str, Any]]
RecordDigester = Callable[[Mapping[str, Any]], str]

MAX_RESOLUTION_RECORD_BYTES = 256 * 1024
MAX_RESOLVED_PREPARATION_RECORD_BYTES = 256 * 1024
MAX_OPERATION_RECORD_BYTES = 64 * 1024


def _safe_session_id(session_id: str) -> str:
    return (
        "".join(
            character if character.isalnum() or character in {"-", "_"} else "-"
            for character in session_id
        ).strip("-")
        or "default"
    )


def _store_root(*, session_id: str, env: EnvironmentConfig) -> Path:
    home_root = resolve_home_root(env=env)
    data_root = resolve_data_root(
        home_root,
        data_root=env.openminion_data_root or None,
        env=env.values,
    )
    return (
        Path(data_root) / "blockchain" / "preparations" / _safe_session_id(session_id)
    )


def _session_store_root(*, session_id: str, env: EnvironmentConfig) -> Path:
    if not session_id:
        raise SessionRecordError("session id is required", reason="session_required")
    home_root = resolve_home_root(env=env)
    data_root = resolve_data_root(
        home_root,
        data_root=env.openminion_data_root or None,
        env=env.values,
    )
    session_key = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
    return Path(data_root) / "blockchain" / "sessions" / session_key


def _digest_token(value: str, *, label: str) -> str:
    digest = str(value)
    if not digest.startswith("sha256:"):
        raise SessionRecordError(f"invalid {label}")
    token = digest.removeprefix("sha256:")
    if len(token) != 64 or any(
        character not in "0123456789abcdef" for character in token
    ):
        raise SessionRecordError(f"invalid {label}")
    return token


def _session_record_path(
    collection: str,
    record_digest: str,
    *,
    session_id: str,
    env: EnvironmentConfig,
) -> Path:
    token = _digest_token(record_digest, label=f"{collection} digest")
    return (
        _session_store_root(session_id=session_id, env=env)
        / collection
        / f"{token}.json"
    )


def _validated_record(
    record: Mapping[str, Any],
    *,
    path_digest: str,
    path_digest_field: str,
    integrity_digest_field: str,
    validator: RecordValidator,
    digester: RecordDigester,
) -> dict[str, Any]:
    try:
        normalized = dict(validator(record))
        stored_path_digest = str(normalized[path_digest_field])
        stored_integrity_digest = str(normalized[integrity_digest_field])
        computed_integrity_digest = str(digester(normalized))
    except (KeyError, TypeError, ValueError) as exc:
        raise SessionRecordError("session record is invalid") from exc
    _digest_token(stored_path_digest, label=path_digest_field)
    _digest_token(stored_integrity_digest, label=integrity_digest_field)
    _digest_token(computed_integrity_digest, label=integrity_digest_field)
    if (
        stored_path_digest != path_digest
        or stored_integrity_digest != computed_integrity_digest
    ):
        raise SessionRecordError("session record digest does not match")
    return normalized


def _serialized_record(record: Mapping[str, Any], *, max_bytes: int) -> bytes:
    try:
        encoded = json.dumps(
            record,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise SessionRecordError("session record is invalid") from exc
    if len(encoded) > max_bytes:
        raise SessionRecordError(
            "session record exceeds size limit",
            reason="size_limit",
        )
    return encoded


def _write_temporary(path: Path, content: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return temporary


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _replace_record(path: Path, content: bytes) -> None:
    temporary = _write_temporary(path, content)
    try:
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _claim_record(path: Path, content: bytes) -> bool:
    temporary = _write_temporary(path, content)
    try:
        try:
            os.link(temporary, path)
        except FileExistsError:
            return False
        _fsync_directory(path.parent)
        return True
    finally:
        temporary.unlink(missing_ok=True)


def _save_session_record(
    collection: str,
    record: Mapping[str, Any],
    context: Any,
    *,
    path_digest_field: str,
    integrity_digest_field: str,
    validator: RecordValidator,
    digester: RecordDigester,
    max_bytes: int,
) -> None:
    session_id = str(getattr(context, "session_id", "") or "")
    env = resolve_environment_config(env=getattr(context, "env", None))
    path_digest = str(record.get(path_digest_field, ""))
    normalized = _validated_record(
        record,
        path_digest=path_digest,
        path_digest_field=path_digest_field,
        integrity_digest_field=integrity_digest_field,
        validator=validator,
        digester=digester,
    )
    path = _session_record_path(
        collection,
        path_digest,
        session_id=session_id,
        env=env,
    )
    _replace_record(path, _serialized_record(normalized, max_bytes=max_bytes))


def _load_session_record(
    collection: str,
    record_digest: str,
    *,
    session_id: str,
    env: EnvironmentConfig | Mapping[str, object] | None,
    path_digest_field: str,
    integrity_digest_field: str,
    validator: RecordValidator,
    digester: RecordDigester,
    max_bytes: int,
) -> dict[str, Any]:
    resolved_env = resolve_environment_config(env=env)
    path = _session_record_path(
        collection,
        record_digest,
        session_id=session_id,
        env=resolved_env,
    )
    try:
        if path.stat().st_size > max_bytes:
            raise SessionRecordError(
                "session record exceeds size limit",
                reason="size_limit",
            )
        with path.open("rb") as stream:
            content = stream.read(max_bytes + 1)
    except OSError as exc:
        raise SessionRecordError(
            "session record is unavailable",
            reason="unavailable",
        ) from exc
    if len(content) > max_bytes:
        raise SessionRecordError(
            "session record exceeds size limit",
            reason="size_limit",
        )
    try:
        raw = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SessionRecordError("session record is invalid") from exc
    if not isinstance(raw, Mapping):
        raise SessionRecordError("session record is invalid")
    return _validated_record(
        raw,
        path_digest=record_digest,
        path_digest_field=path_digest_field,
        integrity_digest_field=integrity_digest_field,
        validator=validator,
        digester=digester,
    )


def save_resolution_record(
    record: Mapping[str, Any],
    context: Any,
    *,
    validator: RecordValidator,
    digester: RecordDigester,
) -> None:
    _save_session_record(
        "resolutions",
        record,
        context,
        path_digest_field="resolution_digest",
        integrity_digest_field="resolution_digest",
        validator=validator,
        digester=digester,
        max_bytes=MAX_RESOLUTION_RECORD_BYTES,
    )


def load_resolution_record(
    resolution_digest: str,
    *,
    session_id: str,
    env: EnvironmentConfig | Mapping[str, object] | None = None,
    validator: RecordValidator,
    digester: RecordDigester,
) -> dict[str, Any]:
    return _load_session_record(
        "resolutions",
        resolution_digest,
        session_id=session_id,
        env=env,
        path_digest_field="resolution_digest",
        integrity_digest_field="resolution_digest",
        validator=validator,
        digester=digester,
        max_bytes=MAX_RESOLUTION_RECORD_BYTES,
    )


def save_resolved_preparation_record(
    record: Mapping[str, Any],
    context: Any,
    *,
    validator: RecordValidator,
    digester: RecordDigester,
) -> None:
    _save_session_record(
        "preparations",
        record,
        context,
        path_digest_field="preparation_digest",
        integrity_digest_field="preparation_digest",
        validator=validator,
        digester=digester,
        max_bytes=MAX_RESOLVED_PREPARATION_RECORD_BYTES,
    )


def load_resolved_preparation_record(
    preparation_digest: str,
    *,
    session_id: str,
    env: EnvironmentConfig | Mapping[str, object] | None = None,
    validator: RecordValidator,
    digester: RecordDigester,
) -> dict[str, Any]:
    return _load_session_record(
        "preparations",
        preparation_digest,
        session_id=session_id,
        env=env,
        path_digest_field="preparation_digest",
        integrity_digest_field="preparation_digest",
        validator=validator,
        digester=digester,
        max_bytes=MAX_RESOLVED_PREPARATION_RECORD_BYTES,
    )


def claim_operation_record(
    record: Mapping[str, Any],
    context: Any,
    *,
    validator: RecordValidator,
    digester: RecordDigester,
) -> bool:
    session_id = str(getattr(context, "session_id", "") or "")
    env = resolve_environment_config(env=getattr(context, "env", None))
    preparation_digest = str(record.get("preparation_digest", ""))
    normalized = _validated_record(
        record,
        path_digest=preparation_digest,
        path_digest_field="preparation_digest",
        integrity_digest_field="operation_digest",
        validator=validator,
        digester=digester,
    )
    path = _session_record_path(
        "operations",
        preparation_digest,
        session_id=session_id,
        env=env,
    )
    return _claim_record(
        path,
        _serialized_record(normalized, max_bytes=MAX_OPERATION_RECORD_BYTES),
    )


def load_operation_record(
    preparation_digest: str,
    *,
    session_id: str,
    env: EnvironmentConfig | Mapping[str, object] | None = None,
    validator: RecordValidator,
    digester: RecordDigester,
) -> dict[str, Any]:
    return _load_session_record(
        "operations",
        preparation_digest,
        session_id=session_id,
        env=env,
        path_digest_field="preparation_digest",
        integrity_digest_field="operation_digest",
        validator=validator,
        digester=digester,
        max_bytes=MAX_OPERATION_RECORD_BYTES,
    )


def replace_operation_record(
    record: Mapping[str, Any],
    context: Any,
    *,
    validator: RecordValidator,
    digester: RecordDigester,
) -> None:
    session_id = str(getattr(context, "session_id", "") or "")
    env = resolve_environment_config(env=getattr(context, "env", None))
    preparation_digest = str(record.get("preparation_digest", ""))
    path = _session_record_path(
        "operations",
        preparation_digest,
        session_id=session_id,
        env=env,
    )
    if not path.is_file():
        raise SessionRecordError(
            "operation record is unavailable",
            reason="unavailable",
        )
    normalized = _validated_record(
        record,
        path_digest=preparation_digest,
        path_digest_field="preparation_digest",
        integrity_digest_field="operation_digest",
        validator=validator,
        digester=digester,
    )
    _replace_record(
        path, _serialized_record(normalized, max_bytes=MAX_OPERATION_RECORD_BYTES)
    )


def _reference_path(
    preparation_digest: str,
    *,
    session_id: str,
    env: EnvironmentConfig,
) -> Path:
    digest = str(preparation_digest).removeprefix("sha256:")
    if len(digest) != 64 or any(
        character not in "0123456789abcdef" for character in digest
    ):
        raise PreparationReferenceError("invalid preparation digest")
    return _store_root(session_id=session_id, env=env) / f"{digest}.json"


def save_prepared_transaction(
    prepared: Mapping[str, Any],
    context: Any,
) -> None:
    session_id = str(getattr(context, "session_id", "") or "").strip()
    if not session_id:
        return
    env = resolve_environment_config(env=getattr(context, "env", None))
    payload = SEND_REQUEST_ADAPTER.validate_python(
        {
            "transaction": prepared["transaction"],
            "call_context": prepared["call_context"],
            "preparation_digest": prepared["preparation_digest"],
        }
    ).model_dump(mode="json")
    path = _reference_path(
        payload["preparation_digest"],
        session_id=session_id,
        env=env,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    temporary.replace(path)
    latest = path.parent / "latest"
    latest.write_text(payload["preparation_digest"], encoding="utf-8")


def resolve_prepared_transaction(
    args: Mapping[str, Any],
    *,
    session_id: str,
    env: EnvironmentConfig | Mapping[str, object] | None = None,
) -> dict[str, Any]:
    if args.get("kind") == "resolved_contract_call":
        return RESOLVED_PREPARATION_ADAPTER.validate_python(args).model_dump(mode="json")
    if "transaction" in args:
        return SEND_REQUEST_ADAPTER.validate_python(dict(args)).model_dump(mode="json")
    resolved_env = resolve_environment_config(env=env)
    digest = str(args.get("preparation_digest", "") or "")
    if not digest:
        store_root = _store_root(session_id=session_id, env=resolved_env)
        try:
            digest = (store_root / "latest").read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise PreparationReferenceError(
                "prepared transaction is unavailable"
            ) from exc
    path = _reference_path(digest, session_id=session_id, env=resolved_env)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        try:
            return load_resolved_preparation_record(
                digest,
                session_id=session_id,
                env=resolved_env,
                validator=validate_resolved_preparation_record,
                digester=resolved_preparation_digest,
            )
        except SessionRecordError as exc:
            raise PreparationReferenceError(
                "prepared transaction is unavailable"
            ) from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise PreparationReferenceError("prepared transaction is unavailable") from exc
    try:
        prepared = SEND_REQUEST_ADAPTER.validate_python(payload).model_dump(mode="json")
    except (TypeError, ValueError) as exc:
        raise PreparationReferenceError("prepared transaction is invalid") from exc
    if prepared["preparation_digest"] != digest:
        raise PreparationReferenceError("prepared transaction digest does not match")
    return prepared


__all__ = [
    "MAX_OPERATION_RECORD_BYTES",
    "MAX_RESOLUTION_RECORD_BYTES",
    "MAX_RESOLVED_PREPARATION_RECORD_BYTES",
    "PreparationReferenceError",
    "SessionRecordError",
    "claim_operation_record",
    "load_operation_record",
    "load_resolution_record",
    "load_resolved_preparation_record",
    "replace_operation_record",
    "resolve_prepared_transaction",
    "save_resolution_record",
    "save_resolved_preparation_record",
    "save_prepared_transaction",
]
