"""Command metadata helpers for the brain tool adapter."""

from collections.abc import Mapping
from typing import Any

_POLICY_REPLAY_SOURCE = "policy_replay"
_CONFIRMATION_SOURCE_METADATA_KEY = "confirmation_source"
_CONFIRMATION_GRANT_ID_METADATA_KEY = "confirmation_grant_id"


def _first_non_empty(source: Mapping[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = source.get(key)
        token = str(value or "").strip()
        if token:
            return token
    return ""


def _confirmation_replay_metadata(inputs: Any) -> dict[str, str]:
    if not isinstance(inputs, Mapping):
        return {}
    grant_id = str(inputs.get("confirmation_grant_id", "") or "").strip()
    source = str(inputs.get("confirmation_source", "") or "").strip()
    if not grant_id or source != _POLICY_REPLAY_SOURCE:
        return {}
    return {
        _CONFIRMATION_SOURCE_METADATA_KEY: _POLICY_REPLAY_SOURCE,
        _CONFIRMATION_GRANT_ID_METADATA_KEY: grant_id,
    }


def _coerce_message_ref(candidate: Any) -> dict[str, str] | None:
    if not isinstance(candidate, Mapping):
        return None
    payload = dict(candidate)
    channel = _first_non_empty(payload, ("channel", "provider"))
    conversation_id = _first_non_empty(
        payload,
        (
            "conversation_id",
            "conversationId",
            "chat_id",
            "chatId",
            "chat_key",
            "chatKey",
            "target",
        ),
    )
    message_id = _first_non_empty(
        payload, ("message_id", "messageId", "source_message_id", "id")
    )
    account_id = _first_non_empty(payload, ("account_id", "accountId", "account"))

    if not channel or not conversation_id or not message_id:
        return None
    out = {
        "channel": channel,
        "conversation_id": conversation_id,
        "message_id": message_id,
    }
    if account_id:
        out["account_id"] = account_id
    return out


def _extract_runtime_message_ref(
    command: Mapping[str, Any], args: Mapping[str, Any]
) -> dict[str, str] | None:
    candidates: list[Any] = [
        args.get("message"),
        command.get("message"),
        command.get("message_ref"),
    ]
    inputs = command.get("inputs")
    if isinstance(inputs, Mapping):
        candidates.extend(
            [
                inputs.get("message"),
                inputs.get("message_ref"),
                inputs.get("source_message"),
                inputs,
            ]
        )
    meta = command.get("meta")
    if isinstance(meta, Mapping):
        candidates.extend([meta.get("message"), meta.get("message_ref"), meta])
    for candidate in candidates:
        message_ref = _coerce_message_ref(candidate)
        if message_ref is not None:
            return message_ref
    return None


def _orchestration_metadata_from_command(command: Mapping[str, Any]) -> dict[str, Any]:
    meta = command.get("meta")
    if not isinstance(meta, Mapping):
        return {}
    orchestration = meta.get("orchestration")
    if not isinstance(orchestration, Mapping):
        return {}
    return {
        str(key): value
        for key, value in orchestration.items()
        if str(key or "").strip()
    }


def _merge_orchestration_context_metadata(
    policy_raw: dict[str, Any],
    orchestration_metadata: Mapping[str, Any] | None,
) -> None:
    context_metadata = policy_raw.get("context_metadata")
    if isinstance(context_metadata, Mapping):
        if not isinstance(context_metadata, dict):
            context_metadata = dict(context_metadata)
            policy_raw["context_metadata"] = context_metadata
    else:
        context_metadata = {}
        policy_raw["context_metadata"] = context_metadata
    if not orchestration_metadata:
        return
    existing = context_metadata.get("orchestration")
    if isinstance(existing, Mapping):
        merged = dict(existing)
        merged.update(dict(orchestration_metadata))
    else:
        merged = dict(orchestration_metadata)
    context_metadata["orchestration"] = merged


__all__ = [
    "_confirmation_replay_metadata",
    "_extract_runtime_message_ref",
    "_merge_orchestration_context_metadata",
    "_orchestration_metadata_from_command",
]
