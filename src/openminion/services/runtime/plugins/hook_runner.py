from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from typing import Iterable, List, Tuple

from openminion.base.types import AgentResponse, Message
from openminion.services.runtime.plugins.hooks import Plugin, PluginContext

HOOK_MODE_MUTATING = "mutating"
HOOK_MODE_SIDE_EFFECT = "side_effect"


class PluginHookRunner:
    def __init__(self, max_parallel_workers: int = 8) -> None:
        self._max_parallel_workers = max(1, int(max_parallel_workers))

    def run_inbound(
        self,
        plugins: Iterable[Plugin],
        message: Message,
        context: PluginContext,
    ) -> Message:
        mutating_plugins, side_effect_plugins = self._partition_plugins(
            plugins, inbound=True, context=context
        )

        current = message
        for plugin in mutating_plugins:
            try:
                current = plugin.on_message(current, context)
            except Exception:
                context.logger.exception(
                    "plugin inbound mutating hook failed plugin=%s",
                    plugin_label(plugin),
                )

        self._run_side_effect_inbound(side_effect_plugins, current, context)
        return current

    def run_outbound(
        self,
        plugins: Iterable[Plugin],
        response: AgentResponse,
        message: Message,
        context: PluginContext,
    ) -> AgentResponse:
        mutating_plugins, side_effect_plugins = self._partition_plugins(
            plugins, inbound=False, context=context
        )

        current = response
        for plugin in mutating_plugins:
            try:
                current = plugin.on_response(current, message, context)
            except Exception:
                context.logger.exception(
                    "plugin outbound mutating hook failed plugin=%s",
                    plugin_label(plugin),
                )

        self._run_side_effect_outbound(side_effect_plugins, current, message, context)
        return current

    def _partition_plugins(
        self,
        plugins: Iterable[Plugin],
        *,
        inbound: bool,
        context: PluginContext,
    ) -> Tuple[List[Plugin], List[Plugin]]:
        mutating_plugins: List[Plugin] = []
        side_effect_plugins: List[Plugin] = []
        for plugin in plugins:
            mode = _resolve_hook_mode(plugin=plugin, inbound=inbound, context=context)
            if mode == HOOK_MODE_SIDE_EFFECT:
                side_effect_plugins.append(plugin)
            else:
                mutating_plugins.append(plugin)
        return mutating_plugins, side_effect_plugins

    def _run_side_effect_inbound(
        self,
        plugins: List[Plugin],
        message: Message,
        context: PluginContext,
    ) -> None:
        if not plugins:
            return

        max_workers = min(self._max_parallel_workers, len(plugins))
        with ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="openminion-plugin"
        ) as executor:
            futures: dict[Future[Message], Plugin] = {}
            for plugin in plugins:
                snapshot = _clone_message(message)
                future = executor.submit(plugin.on_message, snapshot, context)
                futures[future] = plugin

            for future in as_completed(futures):
                plugin = futures[future]
                try:
                    future.result()
                except Exception:
                    context.logger.exception(
                        "plugin inbound side-effect hook failed plugin=%s",
                        plugin_label(plugin),
                    )

    def _run_side_effect_outbound(
        self,
        plugins: List[Plugin],
        response: AgentResponse,
        message: Message,
        context: PluginContext,
    ) -> None:
        if not plugins:
            return

        max_workers = min(self._max_parallel_workers, len(plugins))
        with ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="openminion-plugin"
        ) as executor:
            futures: dict[Future[AgentResponse], Plugin] = {}
            for plugin in plugins:
                response_snapshot = _clone_response(response)
                message_snapshot = _clone_message(message)
                future = executor.submit(
                    plugin.on_response, response_snapshot, message_snapshot, context
                )
                futures[future] = plugin

            for future in as_completed(futures):
                plugin = futures[future]
                try:
                    future.result()
                except Exception:
                    context.logger.exception(
                        "plugin outbound side-effect hook failed plugin=%s",
                        plugin_label(plugin),
                    )


def _resolve_hook_mode(plugin: Plugin, *, inbound: bool, context: PluginContext) -> str:
    raw_mode = (
        getattr(plugin, "inbound_hook_mode", None)
        if inbound
        else getattr(plugin, "outbound_hook_mode", None)
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
        "plugin hook mode is invalid; defaulting to mutating plugin=%s mode=%s direction=%s",
        plugin_label(plugin),
        str(raw_mode),
        "inbound" if inbound else "outbound",
    )
    return HOOK_MODE_MUTATING


def plugin_label(plugin: Plugin) -> str:
    name = str(getattr(plugin, "name", "")).strip()
    if name:
        return name
    return plugin.__class__.__name__


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
