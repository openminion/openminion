import logging
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Iterable

from openminion.base.config import OpenMinionConfig
from openminion.base.types import AgentResponse, Message

if TYPE_CHECKING:
    from openminion.modules.llm.providers.registry import ProviderRegistry
    from openminion.modules.tool.registry import ToolRegistry


HOOK_MODE_MUTATING = "mutating"
HOOK_MODE_SIDE_EFFECT = "side_effect"


# Closed-set lifecycle event taxonomy aligned with Claude Code hooks.
LIFECYCLE_EVENT_PRE_TOOL_USE = "pre_tool_use"
LIFECYCLE_EVENT_POST_TOOL_USE = "post_tool_use"
LIFECYCLE_EVENT_SESSION_START = "session_start"
LIFECYCLE_EVENT_SESSION_STOP = "session_stop"
LIFECYCLE_EVENT_ON_ERROR = "on_error"
LIFECYCLE_EVENT_ON_SUBAGENT_STOP = "on_subagent_stop"

LIFECYCLE_EVENT_TYPES: frozenset[str] = frozenset(
    {
        LIFECYCLE_EVENT_PRE_TOOL_USE,
        LIFECYCLE_EVENT_POST_TOOL_USE,
        LIFECYCLE_EVENT_SESSION_START,
        LIFECYCLE_EVENT_SESSION_STOP,
        LIFECYCLE_EVENT_ON_ERROR,
        LIFECYCLE_EVENT_ON_SUBAGENT_STOP,
    }
)


@dataclass
class HookContext:
    config: OpenMinionConfig
    logger: logging.Logger


class Hook:
    name = "hook"
    inbound_hook_mode = "mutating"
    outbound_hook_mode = "mutating"

    def on_message(self, message: Message, context: HookContext) -> Message:
        return message

    def on_response(
        self,
        response: AgentResponse,
        message: Message,
        context: HookContext,
    ) -> AgentResponse:
        return response

    def register_tools(self, registry: "ToolRegistry", context: HookContext) -> None:
        del registry, context

    def register_providers(
        self, registry: "ProviderRegistry", context: HookContext
    ) -> None:
        del registry, context


class HookRunner:
    def __init__(self, max_parallel_workers: int = 8) -> None:
        self._max_parallel_workers = max(1, int(max_parallel_workers))

    def run_inbound(
        self,
        hooks: Iterable[Hook],
        message: Message,
        context: HookContext,
    ) -> Message:
        mutating_hooks, side_effect_hooks = self._partition_hooks(
            hooks, inbound=True, context=context
        )

        current = message
        for hook in mutating_hooks:
            try:
                current = hook.on_message(current, context)
            except Exception:
                context.logger.exception(
                    "hook inbound mutating failed hook=%s", _hook_label(hook)
                )

        self._run_side_effect_inbound(side_effect_hooks, current, context)
        return current

    def run_outbound(
        self,
        hooks: Iterable[Hook],
        response: AgentResponse,
        message: Message,
        context: HookContext,
    ) -> AgentResponse:
        mutating_hooks, side_effect_hooks = self._partition_hooks(
            hooks, inbound=False, context=context
        )

        current = response
        for hook in mutating_hooks:
            try:
                current = hook.on_response(current, message, context)
            except Exception:
                context.logger.exception(
                    "hook outbound mutating failed hook=%s", _hook_label(hook)
                )

        self._run_side_effect_outbound(side_effect_hooks, current, message, context)
        return current

    def _partition_hooks(
        self,
        hooks: Iterable[Hook],
        *,
        inbound: bool,
        context: HookContext,
    ) -> tuple[list[Hook], list[Hook]]:
        mutating_hooks: list[Hook] = []
        side_effect_hooks: list[Hook] = []
        for hook in hooks:
            mode = _resolve_hook_mode(hook=hook, inbound=inbound, context=context)
            if mode == HOOK_MODE_SIDE_EFFECT:
                side_effect_hooks.append(hook)
            else:
                mutating_hooks.append(hook)
        return mutating_hooks, side_effect_hooks

    def _run_side_effect_inbound(
        self,
        hooks: list[Hook],
        message: Message,
        context: HookContext,
    ) -> None:
        if not hooks:
            return

        max_workers = min(self._max_parallel_workers, len(hooks))
        with ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="openminion-hook"
        ) as executor:
            futures: dict[Future[Message], Hook] = {}
            for hook in hooks:
                snapshot = _clone_message(message)
                future = executor.submit(hook.on_message, snapshot, context)
                futures[future] = hook

            for future in as_completed(futures):
                hook = futures[future]
                try:
                    future.result()
                except Exception:
                    context.logger.exception(
                        "hook inbound side-effect failed hook=%s",
                        _hook_label(hook),
                    )

    def _run_side_effect_outbound(
        self,
        hooks: list[Hook],
        response: AgentResponse,
        message: Message,
        context: HookContext,
    ) -> None:
        if not hooks:
            return

        max_workers = min(self._max_parallel_workers, len(hooks))
        with ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="openminion-hook"
        ) as executor:
            futures: dict[Future[AgentResponse], Hook] = {}
            for hook in hooks:
                response_snapshot = _clone_response(response)
                message_snapshot = _clone_message(message)
                future = executor.submit(
                    hook.on_response, response_snapshot, message_snapshot, context
                )
                futures[future] = hook

            for future in as_completed(futures):
                hook = futures[future]
                try:
                    future.result()
                except Exception:
                    context.logger.exception(
                        "hook outbound side-effect failed hook=%s",
                        _hook_label(hook),
                    )


class HookRegistry:
    """Hook registration and lookup (was PluginRegistry)."""

    def __init__(
        self,
        hooks: Iterable[Hook] = (),
        runner: HookRunner | None = None,
    ) -> None:
        self._hooks: list[Hook] = list(hooks)
        self._manifests: dict[str, object] = {}
        self._runner = runner or HookRunner()

    def register(self, hook: Hook, manifest: object | None = None) -> None:
        if manifest is not None:
            manifest_id = getattr(manifest, "id", None)
            if manifest_id and manifest_id in self._manifests:
                raise RuntimeError(f"Duplicate hook manifest id: {manifest_id}")
            if manifest_id:
                self._manifests[manifest_id] = manifest
        self._hooks.append(hook)

    def names(self) -> list[str]:
        return [hook.name for hook in self._hooks]

    def manifest_ids(self) -> list[str]:
        return sorted(self._manifests.keys())

    def manifests(self) -> list[object]:
        return [self._manifests[key] for key in sorted(self._manifests.keys())]

    def register_tool_extensions(
        self, registry: "ToolRegistry", context: HookContext
    ) -> None:
        for hook in self._hooks:
            try:
                hook.register_tools(registry, context)
            except Exception:
                context.logger.exception(
                    "hook tool registration failed hook=%s",
                    _hook_label(hook),
                )

    def apply_inbound(self, message: Message, context: HookContext) -> Message:
        return self._runner.run_inbound(self._hooks, message, context)

    def apply_outbound(
        self,
        response: AgentResponse,
        message: Message,
        context: HookContext,
    ) -> AgentResponse:
        return self._runner.run_outbound(self._hooks, response, message, context)


def _hook_label(hook: Hook) -> str:
    name = str(getattr(hook, "name", "")).strip()
    if name:
        return name
    return hook.__class__.__name__


def _resolve_hook_mode(hook: Hook, *, inbound: bool, context: HookContext) -> str:
    raw_mode = (
        getattr(hook, "inbound_hook_mode", None)
        if inbound
        else getattr(hook, "outbound_hook_mode", None)
    )
    normalized = str(raw_mode or "").strip().lower().replace("-", "_")

    if normalized in {"", "mutating", "sequential"}:
        return HOOK_MODE_MUTATING
    if normalized in {
        "side_effect",
        "sideeffect",
        "parallel",
        "read_only",
        "readonly",
        "observe",
        "observational",
    }:
        return HOOK_MODE_SIDE_EFFECT

    context.logger.warning(
        "hook mode is invalid; defaulting to mutating hook=%s mode=%s direction=%s",
        _hook_label(hook),
        str(raw_mode),
        "inbound" if inbound else "outbound",
    )
    return HOOK_MODE_MUTATING


def _clone_message(message: Message) -> Message:
    return Message(
        channel=message.channel,
        target=message.target,
        body=message.body,
        metadata=dict(message.metadata),
        id=message.id,
        timestamp=message.timestamp,
    )


def _clone_response(response: AgentResponse) -> AgentResponse:
    return AgentResponse(
        text=response.text,
        channel=response.channel,
        target=response.target,
        metadata=dict(response.metadata),
    )


def build_default_hook_registry(
    config: OpenMinionConfig, logger: logging.Logger
) -> HookRegistry:
    """Build default hook registry (was build_default_plugin_registry)."""
    return _build_default_hook_registry(
        config=config, logger=logger, on_before_activate=None
    )


def _build_default_hook_registry(
    *,
    config: OpenMinionConfig,
    logger: logging.Logger,
    on_before_activate: Callable[[object], None] | None,
) -> HookRegistry:
    from openminion.services.runtime.plugins import (
        build_default_plugin_registry_with_activation_guard,
        PluginContext,
    )

    registry = HookRegistry()
    enabled = _normalize_enabled_hooks(config.enabled_plugins)
    if not enabled:
        logger.debug("enabled hooks: none")
        return registry

    plugin_logger = logger.getChild("plugins")
    plugins = build_default_plugin_registry_with_activation_guard(
        config=config,
        logger=plugin_logger,
        on_before_activate=on_before_activate,
    )
    PluginContext(config=config, logger=plugin_logger)

    for plugin in plugins._plugins:
        registry.register(plugin)

    logger.debug("enabled hooks: %s", ", ".join(enabled))
    return registry


def build_default_hook_registry_with_activation_guard(
    *,
    config: OpenMinionConfig,
    logger: logging.Logger,
    on_before_activate: Callable[[object], None] | None = None,
) -> HookRegistry:
    """Build the default hook registry with an activation guard."""
    return _build_default_hook_registry(
        config=config,
        logger=logger,
        on_before_activate=on_before_activate,
    )


def _normalize_enabled_hooks(raw_values: list[str]) -> list[str]:
    """Normalize the enabled hook list."""
    normalized: list[str] = []
    seen: set[str] = set()
    for value in raw_values:
        normalized_value = str(value).strip()
        if not normalized_value:
            continue
        if normalized_value in seen:
            continue
        seen.add(normalized_value)
        normalized.append(normalized_value)
    return normalized


@dataclass
class LifecycleEvent:
    """Lifecycle event payload."""

    event_type: str
    timestamp_ms: int = 0
    trace_id: str = ""
    session_id: str = ""
    agent_id: str = ""
    tool_name: str = ""
    tool_args: dict[str, Any] = field(default_factory=dict)
    tool_call_id: str = ""
    tool_ok: bool | None = None
    tool_duration_ms: int | None = None
    tool_content: str = ""
    error_message: str = ""
    subagent_id: str = ""
    source_payload: dict[str, Any] = field(default_factory=dict)


LifecycleHook = Callable[["LifecycleEvent", HookContext], None]


class LifecycleHookRegistry:
    """Lifecyclehookregistry contract."""

    def __init__(self) -> None:
        self._hooks: dict[str, list[LifecycleHook]] = {
            event_type: [] for event_type in LIFECYCLE_EVENT_TYPES
        }

    def register(self, event_type: str, hook: LifecycleHook) -> None:
        if event_type not in LIFECYCLE_EVENT_TYPES:
            raise ValueError(
                f"unknown lifecycle event type: {event_type!r}; "
                f"valid: {sorted(LIFECYCLE_EVENT_TYPES)}"
            )
        if not callable(hook):
            raise TypeError(
                f"lifecycle hook must be callable; got {type(hook).__name__}"
            )
        self._hooks[event_type].append(hook)

    def unregister(self, event_type: str, hook: LifecycleHook) -> bool:
        bucket = self._hooks.get(event_type)
        if not bucket:
            return False
        try:
            bucket.remove(hook)
            return True
        except ValueError:
            return False

    def fire(
        self,
        event: "LifecycleEvent",
        context: HookContext,
    ) -> None:
        event_type = str(event.event_type or "").strip()
        if not event_type:
            return
        bucket = self._hooks.get(event_type)
        if not bucket:
            return
        for hook in bucket:
            try:
                hook(event, context)
            except Exception:
                context.logger.exception(
                    "lifecycle hook raised on %s: hook=%r",
                    event_type,
                    getattr(hook, "__qualname__", repr(hook)),
                )

    def count(self, event_type: str = "") -> int:
        if event_type:
            return len(self._hooks.get(event_type, ()))
        return sum(len(bucket) for bucket in self._hooks.values())

    def reset(self) -> None:
        for bucket in self._hooks.values():
            bucket.clear()


_default_lifecycle_registry: LifecycleHookRegistry | None = None


def get_default_lifecycle_registry() -> LifecycleHookRegistry:
    """Return the process-wide default lifecycle registry."""
    global _default_lifecycle_registry
    if _default_lifecycle_registry is None:
        _default_lifecycle_registry = LifecycleHookRegistry()
    return _default_lifecycle_registry


def reset_default_lifecycle_registry() -> None:
    """Clear the default lifecycle registry (test isolation)."""
    global _default_lifecycle_registry
    _default_lifecycle_registry = None


def fire_lifecycle_event(
    event: LifecycleEvent,
    *,
    config: OpenMinionConfig | None = None,
    logger: logging.Logger | None = None,
) -> None:
    """Producer-side helper: fire a lifecycle event to the default
    registry. Default-safe when no hooks are registered.
    """
    registry = get_default_lifecycle_registry()
    if registry.count() == 0:
        return
    if logger is None:
        logger = logging.getLogger("openminion.lifecycle_hook")
    context = HookContext(config=config, logger=logger)  # type: ignore[arg-type]
    registry.fire(event, context)


__all__ = [
    "Hook",
    "HookContext",
    "HookRegistry",
    "HookRunner",
    "HOOK_MODE_MUTATING",
    "HOOK_MODE_SIDE_EFFECT",
    "LIFECYCLE_EVENT_PRE_TOOL_USE",
    "LIFECYCLE_EVENT_POST_TOOL_USE",
    "LIFECYCLE_EVENT_SESSION_START",
    "LIFECYCLE_EVENT_SESSION_STOP",
    "LIFECYCLE_EVENT_ON_ERROR",
    "LIFECYCLE_EVENT_ON_SUBAGENT_STOP",
    "LIFECYCLE_EVENT_TYPES",
    "LifecycleEvent",
    "LifecycleHook",
    "LifecycleHookRegistry",
    "build_default_hook_registry",
    "build_default_hook_registry_with_activation_guard",
    "fire_lifecycle_event",
    "get_default_lifecycle_registry",
    "reset_default_lifecycle_registry",
]
