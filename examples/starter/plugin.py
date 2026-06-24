from __future__ import annotations

from openminion.base.types import AgentResponse, Message
from openminion.extensions.base import Plugin, PluginContext


class HelloPlugin(Plugin):
    name = "hello"

    def on_message(self, message: Message, context: PluginContext) -> Message:
        metadata = dict(message.metadata)
        metadata["hello_inbound"] = "true"
        metadata["hello_plugin_version"] = "v1"
        context.logger.debug("hello plugin inbound id=%s", message.id)
        return Message(
            channel=message.channel,
            target=message.target,
            body=message.body,
            metadata=metadata,
            id=message.id,
            timestamp=message.timestamp,
        )

    def on_response(
        self,
        response: AgentResponse,
        message: Message,
        context: PluginContext,
    ) -> AgentResponse:
        metadata = dict(response.metadata)
        metadata["hello_outbound"] = "true"
        context.logger.debug("hello plugin outbound source_message_id=%s", message.id)
        return AgentResponse(
            text=f"{response.text}\n\n(hello-plugin footer)",
            channel=response.channel,
            target=response.target,
            metadata=metadata,
        )
