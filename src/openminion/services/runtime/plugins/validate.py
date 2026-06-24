from openminion.base.types import AgentResponse, Message
from openminion.services.runtime.plugins.hooks import Plugin, PluginContext


class ValidatePlugin(Plugin):
    name = "validate"

    def on_message(self, message: Message, context: PluginContext) -> Message:
        context.logger.debug(
            "inbound message id=%s channel=%s target=%s",
            message.id,
            message.channel,
            message.target,
        )
        return message

    def on_response(
        self,
        response: AgentResponse,
        message: Message,
        context: PluginContext,
    ) -> AgentResponse:
        context.logger.debug(
            "outbound response channel=%s target=%s source_message_id=%s",
            response.channel,
            response.target,
            message.id,
        )
        return response
