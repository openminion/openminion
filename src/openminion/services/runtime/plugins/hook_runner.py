from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import replace
from functools import partial

from openminion.base.types import AgentResponse, Message
from openminion.services.runtime.plugins.hooks import Plugin, PluginContext
from openminion.services.runtime.plugins.metadata import plugin_label

HOOK_MODE_MUTATING = "mutating"
HOOK_MODE_SIDE_EFFECT = "side_effect"
_MUTATING_MODES = frozenset({"", "mutating", "sequential"})
_SIDE_EFFECT_MODES = frozenset(
    {
        "side_effect",
        "sideeffect",
        "parallel",
        "read_only",
        "readonly",
        "observe",
        "observational",
    }
)


class PluginHookRunner:
    def __init__(
        self,
        max_parallel_workers: int = 8,
        side_effect_timeout_seconds: float = 5.0,
        event_sink: Callable[[str, dict[str, object]], None] | None = None,
    ) -> None:
        self._max_parallel_workers = max(1, int(max_parallel_workers))
        self._side_effect_timeout_seconds = max(0.0, float(side_effect_timeout_seconds))
        self._failures: dict[str, dict[str, str]] = {}
        self._event_sink = event_sink

    def failure_status(self, plugin: Plugin) -> dict[str, str] | None:
        failure = self._failures.get(_plugin_id(plugin))
        return dict(failure) if failure is not None else None

    def run_inbound(
        self,
        plugins: Iterable[Plugin],
        message: Message,
        context: PluginContext,
    ) -> Message:
        mutating, side_effects = self._partition(plugins, inbound=True, context=context)
        current = message
        for plugin in mutating:
            try:
                current = plugin.on_message(current, context)
            except Exception:
                self._record_failure(
                    plugin,
                    direction="inbound",
                    hook_mode=HOOK_MODE_MUTATING,
                    reason="exception",
                )
                context.logger.exception(
                    "plugin inbound mutating hook failed plugin=%s",
                    plugin_label(plugin),
                )

        jobs = (
            (plugin, partial(plugin.on_message, _copy_message(current), context))
            for plugin in side_effects
        )
        self._run_side_effects(jobs, direction="inbound", context=context)
        return current

    def run_outbound(
        self,
        plugins: Iterable[Plugin],
        response: AgentResponse,
        message: Message,
        context: PluginContext,
    ) -> AgentResponse:
        mutating, side_effects = self._partition(
            plugins, inbound=False, context=context
        )
        current = response
        for plugin in mutating:
            try:
                current = plugin.on_response(current, message, context)
            except Exception:
                self._record_failure(
                    plugin,
                    direction="outbound",
                    hook_mode=HOOK_MODE_MUTATING,
                    reason="exception",
                )
                context.logger.exception(
                    "plugin outbound mutating hook failed plugin=%s",
                    plugin_label(plugin),
                )

        jobs = (
            (
                plugin,
                partial(
                    plugin.on_response,
                    _copy_response(current),
                    _copy_message(message),
                    context,
                ),
            )
            for plugin in side_effects
        )
        self._run_side_effects(jobs, direction="outbound", context=context)
        return current

    def _partition(
        self,
        plugins: Iterable[Plugin],
        *,
        inbound: bool,
        context: PluginContext,
    ) -> tuple[list[Plugin], list[Plugin]]:
        mutating: list[Plugin] = []
        side_effects: list[Plugin] = []
        for plugin in plugins:
            target = (
                side_effects
                if _hook_mode(plugin, inbound=inbound, context=context)
                == HOOK_MODE_SIDE_EFFECT
                else mutating
            )
            target.append(plugin)
        return mutating, side_effects

    def _run_side_effects(
        self,
        jobs: Iterable[tuple[Plugin, Callable[[], object]]],
        *,
        direction: str,
        context: PluginContext,
    ) -> None:
        hook_jobs = list(jobs)
        if not hook_jobs:
            return
        executor = ThreadPoolExecutor(
            max_workers=min(self._max_parallel_workers, len(hook_jobs)),
            thread_name_prefix="openminion-plugin",
        )
        futures = {executor.submit(call): plugin for plugin, call in hook_jobs}
        done, pending = wait(futures, timeout=self._side_effect_timeout_seconds)
        for future in done:
            plugin = futures[future]
            try:
                future.result()
            except Exception:
                self._record_failure(
                    plugin,
                    direction=direction,
                    hook_mode=HOOK_MODE_SIDE_EFFECT,
                    reason="exception",
                )
                context.logger.exception(
                    "plugin %s side-effect hook failed plugin=%s",
                    direction,
                    plugin_label(plugin),
                )
        for future in pending:
            plugin = futures[future]
            self._record_failure(
                plugin,
                direction=direction,
                hook_mode=HOOK_MODE_SIDE_EFFECT,
                reason="timeout",
            )
            context.logger.error(
                "plugin %s side-effect hook timed out plugin=%s",
                direction,
                plugin_label(plugin),
            )
            future.cancel()
        executor.shutdown(wait=False, cancel_futures=True)

    def _record_failure(
        self,
        plugin: Plugin,
        *,
        direction: str,
        hook_mode: str,
        reason: str,
    ) -> None:
        plugin_id = _plugin_id(plugin)
        failure = {
            "plugin_id": plugin_id,
            "direction": direction,
            "hook_mode": hook_mode,
            "reason": reason,
        }
        self._failures[plugin_id] = failure
        if self._event_sink is not None:
            self._event_sink("ext.plugin.hook.degraded", dict(failure))


def _plugin_id(plugin: Plugin) -> str:
    return str(getattr(plugin, "_openminion_plugin_id", "") or plugin_label(plugin))


def _hook_mode(plugin: Plugin, *, inbound: bool, context: PluginContext) -> str:
    raw = (
        getattr(plugin, "inbound_hook_mode", None)
        if inbound
        else getattr(plugin, "outbound_hook_mode", None)
    )
    normalized = str(raw or "").strip().lower().replace("-", "_")
    if normalized in _MUTATING_MODES:
        return HOOK_MODE_MUTATING
    if normalized in _SIDE_EFFECT_MODES:
        return HOOK_MODE_SIDE_EFFECT
    context.logger.warning(
        "plugin hook mode is invalid; defaulting to mutating plugin=%s mode=%s direction=%s",
        plugin_label(plugin),
        str(raw),
        "inbound" if inbound else "outbound",
    )
    return HOOK_MODE_MUTATING


def _copy_message(message: Message) -> Message:
    return replace(message, metadata=dict(message.metadata))


def _copy_response(response: AgentResponse) -> AgentResponse:
    return replace(response, metadata=dict(response.metadata))
