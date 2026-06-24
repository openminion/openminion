import unittest

from openminion.modules.llm.providers.behavior import resolve_behavior_profile
from openminion.modules.llm.providers.tool_calling import (
    ToolCallFallbackSource,
    extract_fallback_tool_calls_from_text,
    extract_openai_message_tool_calls,
    resolve_tool_call_source_precedence,
)


class ProviderToolCallingTests(unittest.TestCase):
    def test_extract_openai_message_tool_calls_supports_legacy_function_call_shape(
        self,
    ) -> None:
        message = {
            "function_call": {
                "name": "weather",
                "arguments": '{"city":"San Francisco"}',
            }
        }

        calls = extract_openai_message_tool_calls(
            message, allowed_tool_names=["weather"]
        )
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].name, "weather")
        self.assertEqual(calls[0].arguments.get("city"), "San Francisco")

    def test_extract_openai_message_tool_calls_rejects_weather_alias(self) -> None:
        message = {
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "weather",
                        "arguments": '{"location":"Canada"}',
                    },
                }
            ]
        }

        calls = extract_openai_message_tool_calls(
            message, allowed_tool_names=["weather"]
        )
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].name, "weather")

    def test_extract_fallback_tool_calls_from_text_rejects_weather_alias(self) -> None:
        text = '{"name":"weather","arguments":{"location":"Canada"}}'
        calls = extract_fallback_tool_calls_from_text(
            text, allowed_tool_names=["weather"]
        )
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].name, "weather")

    def test_extract_fallback_tool_calls_from_prefixed_text_rejects_weather_alias(
        self,
    ) -> None:
        text = 'cortensor35: {"name":"weather","arguments":{"location":"Canada"}}'
        calls = extract_fallback_tool_calls_from_text(
            text, allowed_tool_names=["weather"]
        )
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].name, "weather")

    def test_extract_fallback_tool_calls_supports_minimax_tool_json_shape(self) -> None:
        text = (
            '{"tool":"web.fetch","tool_call_id":"fetch1",'
            '"url":"https://packaging.python.org/en/latest/guides/writing-pyproject-toml/"}\n'
            '{"tool":"file.write","tool_call_id":"write1",'
            '"path":"/tmp/pyproject.toml","content":"[project]\\nname=\\"demo\\"\\n"}'
        )

        calls = extract_fallback_tool_calls_from_text(
            text,
            model_name="MiniMax-M2.7",
            allowed_tool_names=["web.fetch", "file.write"],
        )

        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0].id, "fetch1")
        self.assertEqual(calls[0].name, "web.fetch")
        self.assertEqual(
            calls[0].arguments["url"],
            "https://packaging.python.org/en/latest/guides/writing-pyproject-toml/",
        )
        self.assertEqual(calls[1].id, "write1")
        self.assertEqual(calls[1].name, "file.write")
        self.assertEqual(calls[1].arguments["path"], "/tmp/pyproject.toml")

    def test_alias_resolution_drops_ambiguous_matches(self) -> None:
        message = {
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "weather",
                        "arguments": '{"location":"Canada"}',
                    },
                }
            ]
        }
        calls = extract_openai_message_tool_calls(
            message,
            allowed_tool_names=["weather", "fetch_weather"],
        )
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].name, "weather")

    def test_extract_fallback_tool_calls_rejects_browser_run_alias(self) -> None:
        text = '{"name":"browser.run","arguments":{"url":"https://www.google.com"}}'
        calls = extract_fallback_tool_calls_from_text(
            text,
            allowed_tool_names=["browser.playwright.navigate"],
        )
        self.assertEqual(calls, [])

    def test_source_precedence_preserves_native_calls_when_fallback_empty(self) -> None:
        resolution = resolve_tool_call_source_precedence(
            message_payload={
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "weather",
                            "arguments": '{"location":"Tokyo"}',
                        },
                    }
                ]
            },
            fallback_sources=[
                ToolCallFallbackSource(
                    source="message.reasoning",
                    text="I should call the weather tool next.",
                )
            ],
            provider_name="openai",
            model_name="gpt-4.1-mini",
            allowed_tool_names=["weather"],
            fallback_enabled=True,
        )
        self.assertEqual(resolution.selected_source, "native")
        self.assertEqual(len(resolution.calls), 1)
        self.assertEqual(resolution.calls[0].name, "weather")
        self.assertEqual(resolution.attempted_fallback_sources, [])
        self.assertEqual(
            resolution.skipped_fallback_sources,
            ["message.reasoning"],
        )

    def test_source_precedence_uses_fallback_when_native_empty(self) -> None:
        resolution = resolve_tool_call_source_precedence(
            message_payload={"content": ""},
            fallback_sources=[
                ToolCallFallbackSource(
                    source="message.reasoning",
                    text='{"tool_calls":[{"name":"weather","arguments":{"location":"SF"}}]}',
                )
            ],
            provider_name="openai",
            model_name="gpt-4.1-mini",
            allowed_tool_names=["weather"],
            fallback_enabled=True,
        )
        self.assertEqual(resolution.selected_source, "message.reasoning")
        self.assertEqual(resolution.attempted_fallback_sources, ["message.reasoning"])
        self.assertEqual(len(resolution.calls), 1)
        self.assertEqual(resolution.calls[0].name, "weather")

    def test_source_precedence_skips_fallback_when_disabled(self) -> None:
        resolution = resolve_tool_call_source_precedence(
            message_payload={"content": ""},
            fallback_sources=[
                ToolCallFallbackSource(
                    source="message.content",
                    text='{"tool_calls":[{"name":"weather","arguments":{"location":"SF"}}]}',
                )
            ],
            provider_name="openai",
            model_name="gpt-4.1-mini",
            allowed_tool_names=["weather"],
            fallback_enabled=False,
        )
        self.assertEqual(resolution.selected_source, "none")
        self.assertEqual(resolution.calls, [])
        self.assertEqual(resolution.attempted_fallback_sources, [])
        self.assertEqual(resolution.skipped_fallback_sources, ["message.content"])

    def test_source_precedence_structured_mode_parses_minimax_bracket_envelope(
        self,
    ) -> None:
        resolution = resolve_tool_call_source_precedence(
            message_payload={"content": ""},
            fallback_sources=[
                ToolCallFallbackSource(
                    source="message.content",
                    text=(
                        "[TOOL_CALL]"
                        '{tool => "web.search", args => { --query "latest Iran news" }}'
                        "[/TOOL_CALL]"
                    ),
                )
            ],
            provider_name="openai",
            model_name="MiniMax-M2.7",
            allowed_tool_names=["web.search"],
            fallback_enabled=True,
            fallback_mode="structured",
        )
        self.assertEqual(resolution.selected_source, "message.content")
        self.assertEqual(resolution.attempted_fallback_sources, ["message.content"])
        self.assertEqual(len(resolution.calls), 1)
        self.assertEqual(resolution.calls[0].name, "web.search")
        self.assertEqual(
            resolution.calls[0].arguments,
            {"query": "latest Iran news"},
        )
        self.assertEqual(
            resolution.parse_metadata.get("fallback_parse_mode"),
            "minimax_bracket",
        )

    def test_source_precedence_structured_mode_rejects_json_fallback_payload(
        self,
    ) -> None:
        resolution = resolve_tool_call_source_precedence(
            message_payload={"content": ""},
            fallback_sources=[
                ToolCallFallbackSource(
                    source="message.content",
                    text='{"tool_calls":[{"name":"web.search","arguments":{"query":"latest Iran news"}}]}',
                )
            ],
            provider_name="openai",
            model_name="MiniMax-M2.7",
            allowed_tool_names=["web.search"],
            fallback_enabled=True,
            fallback_mode="structured",
        )
        self.assertEqual(resolution.selected_source, "none")
        self.assertEqual(resolution.calls, [])
        self.assertEqual(resolution.attempted_fallback_sources, ["message.content"])

    def test_source_precedence_structured_mode_parses_plain_cli_tool_command(
        self,
    ) -> None:
        resolution = resolve_tool_call_source_precedence(
            message_payload={"content": ""},
            fallback_sources=[
                ToolCallFallbackSource(
                    source="message.content",
                    text='tool file.read {"path":"README.md"}',
                )
            ],
            provider_name="openai",
            model_name="MiniMax-M2.7",
            allowed_tool_names=["file.read"],
            fallback_enabled=True,
            fallback_mode="structured",
        )
        self.assertEqual(resolution.selected_source, "message.content")
        self.assertEqual(len(resolution.calls), 1)
        self.assertEqual(resolution.calls[0].name, "file.read")
        self.assertEqual(resolution.calls[0].arguments, {"path": "README.md"})
        self.assertEqual(
            resolution.parse_metadata.get("fallback_parse_mode"),
            "cli_command",
        )

    def test_source_precedence_parses_colon_op_json_list(self) -> None:
        resolution = resolve_tool_call_source_precedence(
            message_payload={"content": ""},
            fallback_sources=[
                ToolCallFallbackSource(
                    source="message.content",
                    text=(
                        '[{":op":"web.fetch",":args":{"url":"https://example.com"}},'
                        '{":op":"file.write",":args":{"path":"/tmp/out.txt",'
                        '"content":"ok"}}]'
                    ),
                )
            ],
            provider_name="openai",
            model_name="MiniMax-M2.7",
            allowed_tool_names=["web.fetch", "file.write"],
            fallback_enabled=True,
        )
        self.assertEqual(resolution.selected_source, "message.content")
        self.assertEqual(
            [call.name for call in resolution.calls], ["web.fetch", "file.write"]
        )
        self.assertEqual(resolution.calls[0].arguments, {"url": "https://example.com"})
        self.assertEqual(
            resolution.calls[1].arguments,
            {"path": "/tmp/out.txt", "content": "ok"},
        )

    def test_source_precedence_profile_parser_selection_matches_direct_structured_lane(
        self,
    ) -> None:
        profile = resolve_behavior_profile(
            provider="openai",
            model="MiniMax-M2.7",
            base_url="https://api.minimax.io/v1",
        )
        via_profile = resolve_tool_call_source_precedence(
            message_payload={"content": ""},
            fallback_sources=[
                ToolCallFallbackSource(
                    source="message.content",
                    text=(
                        "[TOOL_CALL]"
                        '{tool => "web.search", args => { --query "latest Iran news" }}'
                        "[/TOOL_CALL]"
                    ),
                )
            ],
            provider_name="openai",
            model_name="MiniMax-M2.7",
            allowed_tool_names=["web.search"],
            fallback_enabled=True,
            parser_plugin_selection=profile.parser_plugin_selection,
            fallback_parser_policy=profile.fallback_parser_policy,
        )
        direct = resolve_tool_call_source_precedence(
            message_payload={"content": ""},
            fallback_sources=[
                ToolCallFallbackSource(
                    source="message.content",
                    text=(
                        "[TOOL_CALL]"
                        '{tool => "web.search", args => { --query "latest Iran news" }}'
                        "[/TOOL_CALL]"
                    ),
                )
            ],
            provider_name="openai",
            model_name="MiniMax-M2.7",
            allowed_tool_names=["web.search"],
            fallback_enabled=True,
            fallback_mode="structured",
        )

        self.assertEqual(via_profile.selected_source, direct.selected_source)
        self.assertEqual(via_profile.parse_metadata, direct.parse_metadata)
        self.assertEqual(len(via_profile.calls), len(direct.calls))
        self.assertEqual(via_profile.calls[0].name, direct.calls[0].name)
