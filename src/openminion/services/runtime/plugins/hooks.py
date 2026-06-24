import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from openminion.base.config import OpenMinionConfig
from openminion.base.types import AgentResponse, Message

if TYPE_CHECKING:
    from openminion.modules.llm.providers.registry import ProviderRegistry
    from openminion.modules.tool.registry import ToolRegistry


@dataclass
class PluginContext:
    config: OpenMinionConfig
    logger: logging.Logger


class Plugin:
    name = "plugin"
    inbound_hook_mode = "mutating"
    outbound_hook_mode = "mutating"

    def on_message(self, message: Message, context: PluginContext) -> Message:
        return message

    def on_response(
        self,
        response: AgentResponse,
        message: Message,
        context: PluginContext,
    ) -> AgentResponse:
        return response

    def register_tools(self, registry: "ToolRegistry", context: PluginContext) -> None:
        del registry, context

    def register_providers(
        self, registry: "ProviderRegistry", context: PluginContext
    ) -> None:
        del registry, context
