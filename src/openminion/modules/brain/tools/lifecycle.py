import fnmatch
import logging
import subprocess
from dataclasses import dataclass, field
from typing import Any, Callable

from openminion.base.config import OpenMinionConfig
from openminion.base.config.settings import SettingsResolver
from openminion.base.config.env.subprocess import build_subprocess_env


@dataclass
class LifecycleContext:
    config: OpenMinionConfig | None
    logger: logging.Logger


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
class LifecycleEvent:
    """Structured payload passed to every `LifecycleHook` callable.

    Fields are default-safe so producers at different firing sites
    (tool dispatch vs session lifecycle vs error paths) can build
    events without contorting around fields the seam doesn't carry.
    """

    event_type: str
    timestamp_ms: int = 0
    trace_id: str = ""
    session_id: str = ""
    agent_id: str = ""
    # Tool-lifecycle event fields (default-safe absent on
    # session/error events).
    tool_name: str = ""
    tool_args: dict[str, Any] = field(default_factory=dict)
    tool_call_id: str = ""
    tool_ok: bool | None = None
    tool_duration_ms: int | None = None
    tool_content: str = ""
    # Error-event field.
    error_message: str = ""
    # Subagent-stop event field.
    subagent_id: str = ""
    # Source payload escape hatch — hooks needing fields not yet
    # in the structured surface can read the raw producer payload.
    source_payload: dict[str, Any] = field(default_factory=dict)


# Hooks observe lifecycle events and cannot alter dispatch results.
LifecycleHook = Callable[["LifecycleEvent", LifecycleContext], None]


class LifecycleHookRegistry:
    """Programmatic registry for observe-only lifecycle hooks."""

    def __init__(self) -> None:
        self._hooks: dict[str, list[LifecycleHook]] = {
            event_type: [] for event_type in LIFECYCLE_EVENT_TYPES
        }

    def register(self, event_type: str, hook: LifecycleHook) -> None:
        """Register a lifecycle hook for a specific event type.

        Unknown event types raise `ValueError` — the
        `LIFECYCLE_EVENT_TYPES` set is closed; new event types
        require a spec amendment.
        """
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
        context: LifecycleContext,
    ) -> None:
        """Fire an event to all hooks registered for its type.

        Hook failures are logged without breaking dispatch or session lifecycle.
        """
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
        """Return the number of registered hooks (total or per
        event type). Useful for tests + diagnostics.
        """
        if event_type:
            return len(self._hooks.get(event_type, ()))
        return sum(len(bucket) for bucket in self._hooks.values())

    def reset(self) -> None:
        """Clear all registered hooks. Useful in tests to ensure
        per-test hook isolation.
        """
        for bucket in self._hooks.values():
            bucket.clear()


_default_lifecycle_registry: LifecycleHookRegistry | None = None
_settings_lifecycle_registrations: set[tuple[str, tuple[str, ...]]] = set()


def get_default_lifecycle_registry() -> LifecycleHookRegistry:
    """Return the process-wide default lifecycle registry.

    Lazy-initialized so import-time side effects stay minimal.
    Producer-side firing sites use this accessor; consumers
    register hooks via
    `get_default_lifecycle_registry().register(...)`.
    """
    global _default_lifecycle_registry
    if _default_lifecycle_registry is None:
        _default_lifecycle_registry = LifecycleHookRegistry()
    return _default_lifecycle_registry


def reset_default_lifecycle_registry() -> None:
    """Clear the default lifecycle registry. Test isolation only —
    do not call from production code.
    """
    global _default_lifecycle_registry
    _default_lifecycle_registry = None
    _settings_lifecycle_registrations.clear()


def _settings_registration_key(
    event_type: str,
    resolver: SettingsResolver,
) -> tuple[str, tuple[str, ...]]:
    return (
        event_type,
        tuple(
            str(path.expanduser().resolve(strict=False))
            for path in resolver.source_paths()
        ),
    )


def _event_env(event: LifecycleEvent) -> dict[str, str]:
    payload = {
        "EVENT_TYPE": event.event_type,
        "TRACE_ID": event.trace_id,
        "SESSION_ID": event.session_id,
        "AGENT_ID": event.agent_id,
        "TOOL_NAME": event.tool_name,
        "TOOL_CALL_ID": event.tool_call_id,
        "TOOL_CONTENT": event.tool_content,
        "ERROR_MESSAGE": event.error_message,
        "SUBAGENT_ID": event.subagent_id,
    }
    if event.tool_ok is not None:
        payload["TOOL_OK"] = "1" if event.tool_ok else "0"
    if event.tool_duration_ms is not None:
        payload["TOOL_DURATION_MS"] = str(event.tool_duration_ms)
    return {key: value for key, value in payload.items() if str(value or "")}


def _settings_hook_matches(event: LifecycleEvent, matcher: str) -> bool:
    pattern = str(matcher or "*").strip() or "*"
    if pattern == "*":
        return True
    tool_name = str(event.tool_name or "").strip()
    return bool(tool_name and fnmatch.fnmatchcase(tool_name, pattern))


def _run_settings_hook_command(
    command: str,
    event: LifecycleEvent,
    context: LifecycleContext,
) -> None:
    completed = subprocess.run(
        command,
        shell=True,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
        env=build_subprocess_env(overlay=_event_env(event)),
    )
    logger = context.logger
    if completed.stdout:
        logger.info(
            "settings lifecycle hook stdout: event=%s stdout=%s",
            event.event_type,
            completed.stdout.rstrip(),
        )
    if completed.stderr:
        logger.warning(
            "settings lifecycle hook stderr: event=%s stderr=%s",
            event.event_type,
            completed.stderr.rstrip(),
        )
    if completed.returncode:
        logger.warning(
            "settings lifecycle hook exited non-zero: event=%s returncode=%s",
            event.event_type,
            completed.returncode,
        )


def register_settings_lifecycle_hooks(
    resolver: SettingsResolver,
    *,
    registry: LifecycleHookRegistry | None = None,
) -> int:
    """Register one settings-backed hook per configured lifecycle event."""
    target_registry = registry or get_default_lifecycle_registry()
    registered = 0
    for event_type in sorted(LIFECYCLE_EVENT_TYPES):
        if not resolver.lifecycle_hooks_for_event(event_type):
            continue
        key = _settings_registration_key(event_type, resolver)
        if key in _settings_lifecycle_registrations:
            continue

        def _settings_hook(
            event: LifecycleEvent,
            context: LifecycleContext,
            *,
            _resolver: SettingsResolver = resolver,
        ) -> None:
            for hook_config in _resolver.lifecycle_hooks_for_event(event.event_type):
                if not _settings_hook_matches(event, hook_config.get("matcher", "*")):
                    continue
                _run_settings_hook_command(hook_config["command"], event, context)

        target_registry.register(event_type, _settings_hook)
        _settings_lifecycle_registrations.add(key)
        registered += 1
    return registered


def _resolve_hook_context_for_lifecycle(
    *,
    config: OpenMinionConfig | None = None,
    logger: logging.Logger | None = None,
) -> LifecycleContext:
    """Build a `LifecycleContext` for lifecycle firing.

    Default-safe: when callers don't have a `LifecycleContext` in scope
    (e.g. tool dispatch deep inside the brain runner), we accept
    optional overrides.
    """
    if logger is None:
        logger = logging.getLogger("openminion.lifecycle_hook")
    return LifecycleContext(config=config, logger=logger)


def fire_lifecycle_event(
    event: LifecycleEvent,
    *,
    config: OpenMinionConfig | None = None,
    logger: logging.Logger | None = None,
) -> None:
    """Producer-side helper: fire a lifecycle event to the default
    registry.

    Default-safe — when no hooks are registered, this is a fast
    no-op. Producer sites can call this unconditionally without
    checking registry state. Hooks observe events without changing outcomes.
    """
    registry = get_default_lifecycle_registry()
    if registry.count() == 0:
        return
    context = _resolve_hook_context_for_lifecycle(config=config, logger=logger)
    registry.fire(event, context)


__all__ = [
    "LIFECYCLE_EVENT_PRE_TOOL_USE",
    "LIFECYCLE_EVENT_POST_TOOL_USE",
    "LIFECYCLE_EVENT_SESSION_START",
    "LIFECYCLE_EVENT_SESSION_STOP",
    "LIFECYCLE_EVENT_ON_ERROR",
    "LIFECYCLE_EVENT_ON_SUBAGENT_STOP",
    "LIFECYCLE_EVENT_TYPES",
    "LifecycleContext",
    "LifecycleEvent",
    "LifecycleHook",
    "LifecycleHookRegistry",
    "fire_lifecycle_event",
    "get_default_lifecycle_registry",
    "register_settings_lifecycle_hooks",
    "reset_default_lifecycle_registry",
]
