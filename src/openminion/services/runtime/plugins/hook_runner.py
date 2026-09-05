from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
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
    def __init__(self, max_parallel_workers: int = 8) -> None:
        self._max_parallel_workers = max(1, int(max_parallel_workers))

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
        with ThreadPoolExecutor(
            max_workers=min(self._max_parallel_workers, len(hook_jobs)),
            thread_name_prefix="openminion-plugin",
        ) as executor:
            futures = {executor.submit(call): plugin for plugin, call in hook_jobs}
            for future in as_completed(futures):
                plugin = futures[future]
                try:
                    future.result()
                except Exception:
                    context.logger.exception(
                        "plugin %s side-effect hook failed plugin=%s",
                        direction,
                        plugin_label(plugin),
                    )


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
