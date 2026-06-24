"""Reaction tool plugin."""

import logging
from collections.abc import Mapping, Sequence
from typing import Any, Dict

from openminion.modules.tool.errors import ToolRuntimeError
from openminion.modules.tool.registry import ToolRegistry, ToolSpec
from openminion.modules.tool.runtime import RuntimeContext

from .constants import (
    REACTIONS_LIST_TOOL,
    REACTIONS_SET_TOOL,
    REMOVE_ALL_WITH_EMPTY_EMOJI_CHANNELS,
    REMOVE_SPECIFIC_EMOJI_CHANNELS,
    REQUIRE_NON_EMPTY_EMOJI_CHANNELS,
)
from .interfaces import REACTIONS_PLUGIN_INTERFACE_VERSION
from .schemas import (
    MessageRef,
    REACTIONS_LIST_INPUT_SCHEMA,
    REACTIONS_LIST_OUTPUT_SCHEMA,
    REACTIONS_SET_INPUT_SCHEMA,
    REACTIONS_SET_OUTPUT_SCHEMA,
    ReactionsListArgs,
    ReactionsListResult,
    ReactionsListRow,
    ReactionsSetArgs,
    ReactionsSetResult,
    normalize_channel_name,
)

_LOG = logging.getLogger(__name__)

_CHANNEL_ADAPTERS: dict[str, Any] = {}

TOOL_DESCRIPTOR: Dict[str, Any] = {
    "name": "reactions",
    "title": "Channel Reactions",
    "description": "Lightweight cross-channel reaction add/remove/list operations.",
    "version": "1.0.0",
    "capabilities": ["write", "read", "channel-actions", "reactions"],
    "risk_spec": {
        "risk_level": "low",
        "side_effects": "message_metadata",
        "default_policy": "allow",
    },
    "methods": [REACTIONS_SET_TOOL, REACTIONS_LIST_TOOL],
}


def _emit_event(
    ctx: RuntimeContext, *, event_name: str, payload: Dict[str, Any]
) -> None:
    event = {"event": event_name, **payload}
    try:
        ctx.write_audit_event(event)
    except Exception as exc:
        _LOG.warning(
            "reactions audit event emission failed: event=%s tool=%s err=%s: %s",
            event_name,
            payload.get("tool"),
            type(exc).__name__,
            exc,
        )
        return


def _truncate(value: Any, *, max_chars: int = 200) -> str:
    text = str(value or "")
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars]}...[truncated]"


def _policy_root(ctx: RuntimeContext) -> Mapping[str, Any]:
    raw = getattr(ctx.policy, "raw", {})
    return raw if isinstance(raw, Mapping) else {}


def _tool_config(ctx: RuntimeContext) -> Mapping[str, Any]:
    root = _policy_root(ctx)
    tools_cfg = root.get("tools", {})
    if not isinstance(tools_cfg, Mapping):
        return {}
    reactions_cfg = tools_cfg.get("reactions", {})
    return reactions_cfg if isinstance(reactions_cfg, Mapping) else {}


def _first_non_empty(candidate: Mapping[str, Any], keys: Sequence[str]) -> str:
    for key in keys:
        value = candidate.get(key)
        token = str(value or "").strip()
        if token:
            return token
    return ""


def _normalize_message_candidate(candidate: Any) -> Dict[str, Any] | None:
    if candidate is None:
        return None
    if isinstance(candidate, MessageRef):
        return candidate.model_dump(exclude_none=True)

    payload: Dict[str, Any]
    if isinstance(candidate, Mapping):
        # Accept wrapper shapes: {"message": {...}} or {"message_ref": {...}}
        if isinstance(candidate.get("message"), Mapping):
            payload = dict(candidate.get("message", {}))
        elif isinstance(candidate.get("message_ref"), Mapping):
            payload = dict(candidate.get("message_ref", {}))
        else:
            payload = dict(candidate)
    else:
        # Dataclass/object fallback support (for integration with message models).
        payload = {
            "channel": getattr(candidate, "channel", None),
            "conversation_id": getattr(candidate, "conversation_id", None),
            "message_id": getattr(candidate, "message_id", None),
            "account_id": getattr(candidate, "account_id", None),
            "target": getattr(candidate, "target", None),
            "id": getattr(candidate, "id", None),
            "chat_key": getattr(candidate, "chat_key", None),
            "meta": getattr(candidate, "meta", None),
            "metadata": getattr(candidate, "metadata", None),
        }

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

    meta = payload.get("meta")
    if isinstance(meta, Mapping):
        if not conversation_id:
            conversation_id = _first_non_empty(
                meta, ("conversation_id", "chat_id", "chat_key", "target")
            )
        if not message_id:
            message_id = _first_non_empty(
                meta, ("message_id", "source_message_id", "id")
            )
        if not account_id:
            account_id = _first_non_empty(meta, ("account_id", "account"))
        if not channel:
            channel = _first_non_empty(meta, ("channel", "provider"))

    metadata = payload.get("metadata")
    if isinstance(metadata, Mapping):
        if not conversation_id:
            conversation_id = _first_non_empty(
                metadata, ("conversation_id", "chat_id", "chat_key", "target")
            )
        if not message_id:
            message_id = _first_non_empty(
                metadata, ("message_id", "source_message_id", "id")
            )
        if not account_id:
            account_id = _first_non_empty(metadata, ("account_id", "account"))
        if not channel:
            channel = _first_non_empty(metadata, ("channel", "provider"))

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


def _resolve_runtime_message_ref(ctx: RuntimeContext) -> Dict[str, Any] | None:
    candidates: list[Any] = []

    for attr in (
        "message_ref",
        "message_context",
        "inbound_message_ref",
        "inbound_message",
        "message",
    ):
        if hasattr(ctx, attr):
            candidates.append(getattr(ctx, attr))

    root = _policy_root(ctx)
    for key in ("message_ref", "message", "runtime_message_ref"):
        if key in root:
            candidates.append(root.get(key))

    reactions_cfg = _tool_config(ctx)
    for key in ("runtime_message_ref", "message_ref", "message"):
        if key in reactions_cfg:
            candidates.append(reactions_cfg.get(key))

    for candidate in candidates:
        normalized = _normalize_message_candidate(candidate)
        if normalized is not None:
            return normalized
    return None


def _nested_bool(node: Mapping[str, Any] | None, keys: tuple[str, ...]) -> bool | None:
    current: Any = node
    for key in keys:
        if not isinstance(current, Mapping):
            return None
        if key not in current:
            return None
        current = current[key]
    if isinstance(current, bool):
        return current
    return None


def _lookup_channel_config(
    channels_cfg: Mapping[str, Any] | None, channel: str
) -> Mapping[str, Any] | None:
    if not isinstance(channels_cfg, Mapping):
        return None
    direct = channels_cfg.get(channel)
    if isinstance(direct, Mapping):
        return direct
    for raw_name, payload in channels_cfg.items():
        if normalize_channel_name(raw_name) == channel and isinstance(payload, Mapping):
            return payload
    return None


def _reaction_write_enabled(ctx: RuntimeContext, channel: str) -> bool:
    enabled = True
    reactions_cfg = _tool_config(ctx)

    global_flag = _nested_bool(reactions_cfg, ("actions", "reactions", "enabled"))
    if global_flag is not None:
        enabled = global_flag

    root_channels = _policy_root(ctx).get("channels", {})
    root_channel_cfg = _lookup_channel_config(
        root_channels if isinstance(root_channels, Mapping) else {}, channel
    )
    root_channel_flag = _nested_bool(
        root_channel_cfg, ("actions", "reactions", "enabled")
    )
    if root_channel_flag is not None:
        enabled = root_channel_flag

    plugin_channels = reactions_cfg.get("channels", {})
    plugin_channel_cfg = _lookup_channel_config(
        plugin_channels if isinstance(plugin_channels, Mapping) else {},
        channel,
    )
    plugin_channel_flag = _nested_bool(
        plugin_channel_cfg, ("actions", "reactions", "enabled")
    )
    if plugin_channel_flag is not None:
        enabled = plugin_channel_flag

    return enabled


def _signal_reaction_notifications_enabled(ctx: RuntimeContext) -> bool:
    reactions_cfg = _tool_config(ctx)
    plugin_channels = reactions_cfg.get("channels", {})
    signal_cfg = _lookup_channel_config(
        plugin_channels if isinstance(plugin_channels, Mapping) else {},
        "signal",
    )
    flag = _nested_bool(signal_cfg, ("reactionNotifications",))
    if flag is not None:
        return flag

    root_channels = _policy_root(ctx).get("channels", {})
    root_signal_cfg = _lookup_channel_config(
        root_channels if isinstance(root_channels, Mapping) else {},
        "signal",
    )
    root_flag = _nested_bool(root_signal_cfg, ("reactionNotifications",))
    return bool(root_flag)


def register_channel_adapter(channel: str, adapter: Any) -> None:
    normalized = normalize_channel_name(channel)
    if not normalized:
        raise ValueError("channel is required")
    _CHANNEL_ADAPTERS[normalized] = adapter


def unregister_channel_adapter(channel: str) -> None:
    normalized = normalize_channel_name(channel)
    if not normalized:
        return
    _CHANNEL_ADAPTERS.pop(normalized, None)


def clear_channel_adapters() -> None:
    _CHANNEL_ADAPTERS.clear()


def _resolve_adapter(ctx: RuntimeContext, channel: str) -> Any | None:
    reactions_cfg = _tool_config(ctx)
    adapters = reactions_cfg.get("adapters", {})
    if isinstance(adapters, Mapping):
        direct = adapters.get(channel)
        if direct is not None:
            return direct
        for raw_channel, adapter in adapters.items():
            if normalize_channel_name(raw_channel) == channel:
                return adapter
    return _CHANNEL_ADAPTERS.get(channel)


def _call_adapter(adapter: Any, method_name: str, *args: Any) -> bool:
    method = getattr(adapter, method_name, None)
    if method is None or not callable(method):
        return False
    method(*args)
    return True


def _coerce_set_result(payload: Dict[str, Any]) -> Dict[str, Any]:
    return ReactionsSetResult.model_validate(payload).model_dump(exclude_none=True)


def _coerce_list_result(payload: Dict[str, Any]) -> Dict[str, Any]:
    return ReactionsListResult.model_validate(payload).model_dump()


def _message_dict(message: MessageRef) -> Dict[str, Any]:
    return message.model_dump(exclude_none=True)


def _noop_set_result(args: ReactionsSetArgs, warning: str) -> Dict[str, Any]:
    return _coerce_set_result(
        {
            "ok": True,
            "applied": {"action": "noop", "emoji": args.emoji},
            "message": _message_dict(args.message),
            "warnings": [warning],
        }
    )


def _dispatch_set(args: ReactionsSetArgs, ctx: RuntimeContext) -> Dict[str, Any]:
    channel = args.message.channel
    emoji = args.emoji
    adapter = _resolve_adapter(ctx, channel)

    if args.remove:
        if channel not in REMOVE_SPECIFIC_EMOJI_CHANNELS:
            return _noop_set_result(args, "remove_not_supported_on_channel")

        if adapter is None:
            return _noop_set_result(args, "adapter_not_configured")

        if channel == "whatsapp":
            if not _call_adapter(adapter, "react_remove_all_bot", args.message):
                return _noop_set_result(args, "adapter_missing_capability")
            return _coerce_set_result(
                {
                    "ok": True,
                    "applied": {"action": "removed_one", "emoji": emoji},
                    "message": _message_dict(args.message),
                    "warnings": [],
                }
            )

        if not _call_adapter(adapter, "react_remove_one", args.message, emoji):
            return _noop_set_result(args, "adapter_missing_capability")
        return _coerce_set_result(
            {
                "ok": True,
                "applied": {"action": "removed_one", "emoji": emoji},
                "message": _message_dict(args.message),
                "warnings": [],
            }
        )

    if not emoji:
        if channel in REQUIRE_NON_EMPTY_EMOJI_CHANNELS:
            raise ToolRuntimeError(
                "INVALID_ARGUMENT", f"Channel '{channel}' requires non-empty emoji"
            )
        if channel not in REMOVE_ALL_WITH_EMPTY_EMOJI_CHANNELS:
            return _noop_set_result(args, "remove_all_not_supported_on_channel")
        if adapter is None:
            return _noop_set_result(args, "adapter_not_configured")
        if not _call_adapter(adapter, "react_remove_all_bot", args.message):
            return _noop_set_result(args, "adapter_missing_capability")
        return _coerce_set_result(
            {
                "ok": True,
                "applied": {"action": "removed_all_bot", "emoji": emoji},
                "message": _message_dict(args.message),
                "warnings": [],
            }
        )

    if adapter is None:
        return _noop_set_result(args, "adapter_not_configured")
    if not _call_adapter(adapter, "react_add", args.message, emoji):
        return _noop_set_result(args, "adapter_missing_capability")
    return _coerce_set_result(
        {
            "ok": True,
            "applied": {"action": "added", "emoji": emoji},
            "message": _message_dict(args.message),
            "warnings": [],
        }
    )


def _normalize_reaction_rows(raw_rows: Any, *, scope: str) -> list[Dict[str, Any]]:
    if not isinstance(raw_rows, Sequence) or isinstance(
        raw_rows, (str, bytes, bytearray)
    ):
        return []

    out: list[Dict[str, Any]] = []
    for item in raw_rows:
        if isinstance(item, Mapping):
            emoji = str(item.get("emoji", "")).strip()
            count_raw = item.get("count", 1)
            reacted_by_bot = bool(item.get("reacted_by_bot", False))
        else:
            emoji = str(item).strip()
            count_raw = 1
            reacted_by_bot = False

        if not emoji:
            continue

        try:
            count = max(0, int(count_raw))
        except (TypeError, ValueError):
            count = 0

        if scope == "bot_only" and not reacted_by_bot:
            continue

        row = ReactionsListRow(emoji=emoji, count=count, reacted_by_bot=reacted_by_bot)
        out.append(row.model_dump())

    return out


def _dispatch_list(args: ReactionsListArgs, ctx: RuntimeContext) -> Dict[str, Any]:
    channel = args.message.channel
    adapter = _resolve_adapter(ctx, channel)
    if adapter is None:
        return _coerce_list_result(
            {"ok": False, "reactions": [], "warnings": ["not_supported_on_channel"]}
        )

    method = getattr(adapter, "list_reactions", None)
    if method is None or not callable(method):
        return _coerce_list_result(
            {"ok": False, "reactions": [], "warnings": ["not_supported_on_channel"]}
        )

    raw_rows = method(args.message, args.scope)
    rows = _normalize_reaction_rows(raw_rows, scope=args.scope)
    return _coerce_list_result({"ok": True, "reactions": rows, "warnings": []})


def emit_signal_reaction_received(ctx: RuntimeContext, payload: Dict[str, Any]) -> bool:
    if not _signal_reaction_notifications_enabled(ctx):
        return False
    _emit_event(
        ctx,
        event_name="signal.reaction_received",
        payload={"channel": "signal", "payload": payload},
    )
    return True


def _h_reactions_set(args: Dict[str, Any], ctx: RuntimeContext) -> Dict[str, Any]:
    incoming = dict(args)
    if "message" not in incoming or incoming.get("message") in ({}, None):
        runtime_message_ref = _resolve_runtime_message_ref(ctx)
        if runtime_message_ref is not None:
            incoming["message"] = runtime_message_ref

    parsed = ReactionsSetArgs.model_validate(incoming)
    request_payload = {
        "tool": REACTIONS_SET_TOOL,
        "message": _message_dict(parsed.message),
        "emoji": parsed.emoji,
        "remove": parsed.remove,
    }
    if parsed.reason:
        request_payload["reason"] = _truncate(parsed.reason)
    _emit_event(ctx, event_name="tool.requested", payload=request_payload)

    try:
        if not _reaction_write_enabled(ctx, parsed.message.channel):
            raise ToolRuntimeError(
                "POLICY_DENIED",
                f"Reaction writes disabled for channel '{parsed.message.channel}'",
                {"rule": "channels.<provider>.actions.reactions.enabled"},
            )
        result = _dispatch_set(parsed, ctx)
    except ToolRuntimeError as exc:
        _emit_event(
            ctx,
            event_name="tool.failed",
            payload={
                "tool": REACTIONS_SET_TOOL,
                "message": _message_dict(parsed.message),
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "details": exc.details,
                },
            },
        )
        raise
    except Exception as exc:
        result = _coerce_set_result(
            {
                "ok": False,
                "applied": {"action": "noop", "emoji": parsed.emoji},
                "message": _message_dict(parsed.message),
                "warnings": ["adapter_error"],
                "error": f"{type(exc).__name__}: {exc}",
            }
        )
        _emit_event(
            ctx,
            event_name="tool.failed",
            payload={
                "tool": REACTIONS_SET_TOOL,
                "message": _message_dict(parsed.message),
                "error": {
                    "code": "UPSTREAM_ERROR",
                    "message": result.get("error", "adapter_error"),
                },
            },
        )
        return result

    event_name = "tool.completed" if result.get("ok", False) else "tool.failed"
    _emit_event(
        ctx,
        event_name=event_name,
        payload={
            "tool": REACTIONS_SET_TOOL,
            "message": _message_dict(parsed.message),
            "applied": result.get("applied", {}),
            "warnings": list(result.get("warnings", [])),
            "error": result.get("error"),
        },
    )
    return result


def _h_reactions_list(args: Dict[str, Any], ctx: RuntimeContext) -> Dict[str, Any]:
    incoming = dict(args)
    if "message" not in incoming or incoming.get("message") in ({}, None):
        runtime_message_ref = _resolve_runtime_message_ref(ctx)
        if runtime_message_ref is not None:
            incoming["message"] = runtime_message_ref

    parsed = ReactionsListArgs.model_validate(incoming)
    _emit_event(
        ctx,
        event_name="tool.requested",
        payload={
            "tool": REACTIONS_LIST_TOOL,
            "message": _message_dict(parsed.message),
            "scope": parsed.scope,
        },
    )
    try:
        result = _dispatch_list(parsed, ctx)
    except Exception as exc:
        _emit_event(
            ctx,
            event_name="tool.failed",
            payload={
                "tool": REACTIONS_LIST_TOOL,
                "message": _message_dict(parsed.message),
                "error": {
                    "code": "UPSTREAM_ERROR",
                    "message": f"{type(exc).__name__}: {exc}",
                },
            },
        )
        return _coerce_list_result(
            {"ok": False, "reactions": [], "warnings": ["adapter_error"]}
        )

    _emit_event(
        ctx,
        event_name="tool.completed",
        payload={
            "tool": REACTIONS_LIST_TOOL,
            "message": _message_dict(parsed.message),
            "ok": result.get("ok", False),
            "warnings": list(result.get("warnings", [])),
            "count": len(result.get("reactions", [])),
        },
    )
    return result


def _register_tool(
    registry: ToolRegistry,
    *,
    name: str,
    args_model: type[Any],
    handler: Any,
    min_scope: str,
    idempotent: bool,
) -> None:
    registry.add(
        ToolSpec(
            name=name,
            args_model=args_model,
            min_scope=min_scope,
            handler=handler,
            dangerous=False,
            idempotent=idempotent,
            tags=("plugin", "reactions", "channel"),
            capabilities=("reactions", "channel-action"),
        )
    )


def register(registry: ToolRegistry) -> None:
    _register_tool(
        registry,
        name=REACTIONS_SET_TOOL,
        args_model=ReactionsSetArgs,
        handler=_h_reactions_set,
        min_scope="WRITE_SAFE",
        idempotent=False,
    )
    _register_tool(
        registry,
        name=REACTIONS_LIST_TOOL,
        args_model=ReactionsListArgs,
        handler=_h_reactions_list,
        min_scope="READ_ONLY",
        idempotent=True,
    )


class ReactionsPlugin:
    tool_id = "reactions"
    contract_version = REACTIONS_PLUGIN_INTERFACE_VERSION
    capabilities = ("reactions", "channel-actions", "write")

    input_schema: Dict[str, Any] = {
        REACTIONS_SET_TOOL: REACTIONS_SET_INPUT_SCHEMA,
        REACTIONS_LIST_TOOL: REACTIONS_LIST_INPUT_SCHEMA,
    }
    output_schema: Dict[str, Any] = {
        REACTIONS_SET_TOOL: REACTIONS_SET_OUTPUT_SCHEMA,
        REACTIONS_LIST_TOOL: REACTIONS_LIST_OUTPUT_SCHEMA,
    }

    def register(self, registry: ToolRegistry) -> None:
        register(registry)

    def healthcheck(self) -> Dict[str, Any]:
        return {
            "ok": True,
            "tool": self.tool_id,
            "registered_methods": [REACTIONS_SET_TOOL, REACTIONS_LIST_TOOL],
            "registered_adapters": sorted(_CHANNEL_ADAPTERS.keys()),
            "descriptor": TOOL_DESCRIPTOR,
        }


__all__ = [
    "ReactionsPlugin",
    "TOOL_DESCRIPTOR",
    "clear_channel_adapters",
    "emit_signal_reaction_received",
    "register",
    "register_channel_adapter",
    "unregister_channel_adapter",
]
