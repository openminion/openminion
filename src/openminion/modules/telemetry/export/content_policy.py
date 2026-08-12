from __future__ import annotations

from openminion.base.config import OTELExporterConfig


def external_sensitive_fields(config: OTELExporterConfig) -> frozenset[str]:
    fields: set[str] = set()
    if config.include_input_messages:
        fields.update(
            {"user_message", "history", "input_messages", "gen_ai.input.messages"}
        )
    if config.include_output_messages or config.include_assistant_body:
        fields.update(
            {"content", "output_messages", "output_text", "gen_ai.output.messages"}
        )
    if config.include_tool_content:
        fields.update(
            {
                "arguments",
                "result",
                "tool_definitions",
                "gen_ai.tool.call.arguments",
                "gen_ai.tool.call.result",
                "gen_ai.tool.definitions",
            }
        )
    return frozenset(fields)


__all__ = ["external_sensitive_fields"]
