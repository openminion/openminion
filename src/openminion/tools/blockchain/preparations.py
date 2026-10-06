from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from openminion.base.config import resolve_data_root, resolve_home_root
from openminion.base.config.env import EnvironmentConfig, resolve_environment_config

from .transaction_schemas import SEND_REQUEST_ADAPTER


class PreparationReferenceError(ValueError):
    pass


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
    return Path(data_root) / "blockchain" / "preparations" / _safe_session_id(session_id)


def _reference_path(
    preparation_digest: str,
    *,
    session_id: str,
    env: EnvironmentConfig,
) -> Path:
    digest = str(preparation_digest).removeprefix("sha256:")
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
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
    "PreparationReferenceError",
    "resolve_prepared_transaction",
    "save_prepared_transaction",
]
