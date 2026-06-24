import unittest
from typing import Any

from openminion.modules.llm.providers.message_payloads import _messages_openai_like
from openminion.modules.llm.providers.base import ProviderToolCall, ProviderToolSpec
from openminion.modules.tool.base import ToolExecutionContext
from openminion.modules.llm.providers.tool_calling import (
    build_tool_schema_name_map,
    build_fallback_tool_call_instruction,
    build_openai_tools_payload,
    detect_raw_tool_payload_json,
    extract_fallback_tool_calls_from_text,
    extract_openai_message_tool_calls,
    normalize_tool_call_strategy,
    normalize_tool_choice,
    remap_provider_tool_call_name,
    supports_fallback_tool_calling,
    supports_native_tool_calling,
)
from openminion.modules.llm.providers.tool_calling.registry import (
    parse_structured_tool_call_envelopes,
)
from openminion.modules.llm.schemas import LLMRequest, Message, ToolSpec


class ToolCallingHelpersTests(unittest.TestCase):
    def test_detect_raw_tool_payload_json_catches_prose_prefixed_command_envelope(
        self,
    ) -> None:
        text = (
            "Let me run verification now.\n"
            "```json\n"
            '{"tool": "exec.run", "arguments": {"command": "python -m pytest"}}\n'
            "```"
        )

        self.assertTrue(detect_raw_tool_payload_json(text))

    def test_normalize_tool_call_strategy_defaults_to_hybrid(self) -> None:
        self.assertEqual(normalize_tool_call_strategy(""), "hybrid")
        self.assertEqual(normalize_tool_call_strategy("unknown"), "hybrid")
        self.assertTrue(supports_native_tool_calling("hybrid"))
        self.assertTrue(supports_fallback_tool_calling("hybrid"))
        self.assertFalse(supports_native_tool_calling("fallback"))

    def test_build_openai_tools_payload(self) -> None:
        payload = build_openai_tools_payload(
            [
                ProviderToolSpec(
                    name="weather",
                    description="Lookup weather by city",
                    parameters={
                        "type": "object",
                        "properties": {"city": {"type": "string"}},
                    },
                    strict=True,
                )
            ]
        )
        self.assertEqual(len(payload), 1)
        function_payload = payload[0]["function"]
        self.assertEqual(function_payload["name"], "weather")
        self.assertEqual(function_payload["parameters"]["type"], "object")
        self.assertTrue(function_payload["strict"])

    def test_build_tool_schema_name_map_normalizes_constrained_provider_names(
        self,
    ) -> None:
        name_map = build_tool_schema_name_map(
            [
                ProviderToolSpec(name="web.search", description="Search"),
                ProviderToolSpec(name="file.list_dir", description="List files"),
                ProviderToolSpec(name="submit_output", description="Submit"),
            ],
            provider_name="openrouter",
            model_name="anthropic/claude-3.5-haiku",
        )

        self.assertEqual(
            name_map.canonical_to_external,
            {
                "file.list_dir": "file_list_dir",
                "web.search": "web_search",
            },
        )
        self.assertEqual(
            name_map.external_to_canonical,
            {
                "file_list_dir": "file.list_dir",
                "web_search": "web.search",
            },
        )

    def test_build_tool_schema_name_map_normalizes_openai_dialect_names(
        self,
    ) -> None:
        name_map = build_tool_schema_name_map(
            [ProviderToolSpec(name="web.search", description="Search")],
            provider_name="openrouter",
            model_name="openai/gpt-4o",
        )
        self.assertTrue(name_map.active)
        self.assertEqual(
            name_map.canonical_to_external,
            {"web.search": "web_search"},
        )
        self.assertEqual(
            name_map.external_to_canonical,
            {"web_search": "web.search"},
        )

    def test_build_openai_tools_payload_can_apply_external_names(self) -> None:
        payload = build_openai_tools_payload(
            [ProviderToolSpec(name="web.search", description="Search", parameters={})],
            canonical_to_external={"web.search": "web_search"},
        )
        self.assertEqual(payload[0]["function"]["name"], "web_search")

    def test_build_openai_tools_payload_strips_null_placeholders(self) -> None:
        payload = build_openai_tools_payload(
            [
                ProviderToolSpec(
                    name="weather",
                    description="Lookup weather by location",
                    parameters={
                        "type": "object",
                        "properties": {
                            "location": {
                                "anyOf": [{"type": "string"}, {"type": "null"}],
                                "default": None,
                                "description": "Preferred place query",
                            },
                            "latitude": {
                                "type": ["number", "null"],
                                "default": None,
                            },
                        },
                    },
                )
            ]
        )
        parameters = payload[0]["function"]["parameters"]
        location_schema = parameters["properties"]["location"]
        latitude_schema = parameters["properties"]["latitude"]
        self.assertEqual(location_schema["type"], "string")
        self.assertNotIn("default", location_schema)
        self.assertEqual(latitude_schema["type"], "number")
        self.assertNotIn("default", latitude_schema)

    def test_extract_openai_message_tool_calls(self) -> None:
        message = {
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "weather",
                        "arguments": '{"city":"Paris"}',
                    },
                }
            ]
        }
        calls = extract_openai_message_tool_calls(
            message, allowed_tool_names=["weather"]
        )
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].id, "call_1")
        self.assertEqual(calls[0].arguments.get("city"), "Paris")

    def test_extract_openai_message_tool_calls_supports_legacy_function_call(
        self,
    ) -> None:
        message = {
            "function_call": {
                "name": "weather",
                "arguments": '{"city":"Paris"}',
            }
        }
        calls = extract_openai_message_tool_calls(
            message, allowed_tool_names=["weather"]
        )
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].name, "weather")
        self.assertEqual(calls[0].arguments.get("city"), "Paris")

    def test_extract_openai_message_tool_calls_allows_submit_output(self) -> None:
        message = {
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "submit_output",
                        "arguments": '{"decision":{"mode":"respond","confidence":1.0,"reason_code":"greeting","answer":"hi"}}',
                    },
                }
            ]
        }
        calls = extract_openai_message_tool_calls(
            message, allowed_tool_names=["submit_output"]
        )
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].name, "submit_output")
        self.assertEqual(calls[0].arguments.get("decision", {}).get("mode"), "respond")

    def test_schema_only_submit_output_instruction_requires_tool_call(self) -> None:
        instruction = build_fallback_tool_call_instruction(
            [
                ProviderToolSpec(
                    name="submit_output",
                    description="Return structured output.",
                    parameters={"type": "object"},
                )
            ]
        )
        self.assertIn("You MUST call `submit_output`", instruction)
        self.assertNotIn("return a normal assistant text response", instruction)

    def test_fallback_instruction_can_apply_external_tool_names(self) -> None:
        instruction = build_fallback_tool_call_instruction(
            [
                ProviderToolSpec(
                    name="web.search",
                    description="Search the web.",
                    parameters={"type": "object"},
                )
            ],
            canonical_to_external={"web.search": "web_search"},
        )
        self.assertIn("- web_search: Search the web.", instruction)
        self.assertNotIn("- web.search:", instruction)

    def test_non_schema_instruction_allows_text_response(self) -> None:
        instruction = build_fallback_tool_call_instruction(
            [
                ProviderToolSpec(
                    name="weather.openmeteo.current",
                    description="Get weather.",
                    parameters={"type": "object"},
                )
            ]
        )
        self.assertIn("return a normal assistant text response", instruction)

    def test_messages_openai_like_schema_only_injects_strict_instruction(self) -> None:
        request = LLMRequest(
            messages=[Message(role="user", content="hi")],
            tools=[
                ToolSpec(
                    name="submit_output",
                    description="Submit structured output",
                    input_schema={"type": "object"},
                )
            ],
            tool_choice={"type": "function", "function": {"name": "submit_output"}},
        )
        messages = _messages_openai_like(request, include_fallback_instruction=True)
        self.assertTrue(messages)
        self.assertEqual(messages[0]["role"], "system")
        self.assertIn("You MUST call `submit_output`", messages[0]["content"])
        self.assertNotIn(
            "return a normal assistant text response", messages[0]["content"]
        )

    def test_messages_openai_like_schema_only_injects_after_system_context(
        self,
    ) -> None:
        request = LLMRequest(
            messages=[
                Message(role="system", content="Primary system context."),
                Message(role="user", content="weather in tokyo"),
            ],
            tools=[
                ToolSpec(
                    name="submit_output",
                    description="Submit structured output",
                    input_schema={"type": "object"},
                )
            ],
            tool_choice={"type": "function", "function": {"name": "submit_output"}},
        )
        messages = _messages_openai_like(request, include_fallback_instruction=True)
        self.assertEqual(messages[0]["role"], "system")
        self.assertEqual(messages[0]["content"], "Primary system context.")
        self.assertEqual(messages[1]["role"], "system")
        self.assertIn("You MUST call `submit_output`", messages[1]["content"])
        self.assertEqual(messages[2]["role"], "user")

    def test_messages_openai_like_mixed_tools_keep_permissive_instruction(self) -> None:
        request = LLMRequest(
            messages=[Message(role="user", content="weather")],
            tools=[
                ToolSpec(
                    name="submit_output",
                    description="Submit structured output",
                    input_schema={"type": "object"},
                ),
                ToolSpec(
                    name="weather.openmeteo.current",
                    description="Get weather",
                    input_schema={"type": "object"},
                ),
            ],
            tool_choice="auto",
        )
        messages = _messages_openai_like(request, include_fallback_instruction=True)
        self.assertTrue(messages)
        self.assertEqual(messages[0]["role"], "system")
        self.assertIn("return a normal assistant text response", messages[0]["content"])

    def test_normalize_tool_choice_can_apply_external_names(self) -> None:
        normalized = normalize_tool_choice(
            {"type": "function", "function": {"name": "web.search"}},
            canonical_to_external={"web.search": "web_search"},
        )
        self.assertEqual(
            normalized,
            {"type": "function", "function": {"name": "web_search"}},
        )

    def test_remap_provider_tool_call_name_recovers_canonical_name(self) -> None:
        self.assertEqual(
            remap_provider_tool_call_name(
                "web_search",
                external_to_canonical={"web_search": "web.search"},
            ),
            "web.search",
        )

    def test_messages_openai_like_native_strategy_suppresses_fallback_instruction(
        self,
    ) -> None:
        request = LLMRequest(
            messages=[Message(role="user", content="hi")],
            tools=[
                ToolSpec(
                    name="submit_output",
                    description="Submit structured output",
                    input_schema={"type": "object"},
                )
            ],
            tool_choice={"type": "function", "function": {"name": "submit_output"}},
        )
        messages = _messages_openai_like(request, include_fallback_instruction=False)
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0]["role"], "system")
        self.assertIn("Schema-only control phase", messages[0]["content"])
        self.assertIn("Use only the `submit_output` tool", messages[0]["content"])
        self.assertNotIn("Tool-calling contract", messages[0]["content"])
        self.assertEqual(messages[1]["role"], "user")
        self.assertEqual(messages[1]["content"], "hi")

    def test_messages_openai_like_schema_only_native_instruction_preserves_compat_note(
        self,
    ) -> None:
        request = LLMRequest(
            messages=[Message(role="user", content="research this")],
            tools=[
                ToolSpec(
                    name="submit_output",
                    description="Submit structured output",
                    input_schema={"type": "object"},
                )
            ],
            tool_choice={"type": "function", "function": {"name": "submit_output"}},
        )
        messages = _messages_openai_like(
            request,
            include_fallback_instruction=False,
            extra_system_instruction="Native tool-calling contract.",
        )

        self.assertEqual(messages[0]["role"], "system")
        self.assertIn("Native tool-calling contract.", messages[0]["content"])
        self.assertIn("Schema-only control phase", messages[0]["content"])
        self.assertIn(
            "Do not call, describe, or wrap any other tool", messages[0]["content"]
        )
        self.assertEqual(messages[1]["role"], "user")

    def test_messages_openai_like_can_collapse_multiple_system_messages(self) -> None:
        request = LLMRequest(
            messages=[
                Message(role="system", content="Primary system context."),
                Message(role="system", content="Structured tool contract."),
                Message(role="user", content="time in UTC"),
            ],
            tools=[
                ToolSpec(
                    name="submit_output",
                    description="Submit structured output",
                    input_schema={"type": "object"},
                )
            ],
            tool_choice={"type": "function", "function": {"name": "submit_output"}},
        )
        messages = _messages_openai_like(
            request,
            include_fallback_instruction=False,
            collapse_system_messages=True,
        )
        self.assertEqual([item["role"] for item in messages], ["system", "user"])
        self.assertIn("Primary system context.", messages[0]["content"])
        self.assertIn("Structured tool contract.", messages[0]["content"])

    def test_messages_openai_like_system_only_after_collapse_adds_continue_turn(
        self,
    ) -> None:
        request = LLMRequest(
            messages=[Message(role="assistant", content="")],
            tools=[
                ToolSpec(
                    name="submit_output",
                    description="Submit structured output",
                    input_schema={"type": "object"},
                )
            ],
            tool_choice={"type": "function", "function": {"name": "submit_output"}},
        )

        messages = _messages_openai_like(
            request,
            include_fallback_instruction=False,
            collapse_system_messages=True,
            extra_system_instruction="Provider compatibility instruction.",
        )

        self.assertEqual([item["role"] for item in messages], ["system", "user"])
        self.assertIn("Provider compatibility instruction.", messages[0]["content"])
        self.assertEqual(messages[1]["content"], "Continue.")

    def test_messages_openai_like_can_apply_external_tool_names_to_fallback_instruction(
        self,
    ) -> None:
        request = LLMRequest(
            messages=[Message(role="user", content="search for ai news")],
            tools=[
                ToolSpec(
                    name="web.search",
                    description="Search the web",
                    input_schema={"type": "object"},
                )
            ],
            tool_choice="auto",
        )

        messages = _messages_openai_like(
            request,
            include_fallback_instruction=True,
            tool_name_overrides={"web.search": "web_search"},
        )

        self.assertEqual(messages[0]["role"], "system")
        self.assertIn("- web_search: Search the web", messages[0]["content"])
        self.assertNotIn("- web.search:", messages[0]["content"])

    def test_messages_openai_like_replays_tool_history_with_tool_call_stub(
        self,
    ) -> None:
        request = LLMRequest(
            messages=[
                Message(role="system", content="sys"),
                Message(role="user", content="inspect project"),
                Message(
                    role="tool",
                    content='{"status":"success","summary":"listed files"}',
                    meta={
                        "tool_call_id": "call-1",
                        "tool_name": "file.list_dir",
                        "tool_arguments": {"path": "/tmp"},
                    },
                ),
            ]
        )

        messages = _messages_openai_like(request, include_fallback_instruction=False)

        self.assertEqual(messages[0], {"role": "system", "content": "sys"})
        self.assertEqual(messages[1], {"role": "user", "content": "inspect project"})
        self.assertEqual(messages[2]["role"], "assistant")
        self.assertEqual(messages[2]["content"], "Tool call issued.")
        self.assertEqual(
            messages[2]["tool_calls"][0],
            {
                "id": "call-1",
                "type": "function",
                "function": {
                    "name": "file.list_dir",
                    "arguments": '{"path": "/tmp"}',
                },
            },
        )
        self.assertEqual(
            messages[3],
            {
                "role": "tool",
                "content": '{"status":"success","summary":"listed files"}',
                "tool_call_id": "call-1",
            },
        )

    def test_messages_openai_like_does_not_project_orphan_tool_history_as_assistant(
        self,
    ) -> None:
        request = LLMRequest(
            messages=[
                Message(role="user", content="inspect project"),
                Message(
                    role="tool",
                    content='{"status":"success","summary":"listed files"}',
                    meta={"tool_name": "file.list_dir"},
                ),
            ]
        )

        messages = _messages_openai_like(request, include_fallback_instruction=False)

        self.assertEqual(messages[0], {"role": "user", "content": "inspect project"})
        self.assertEqual(messages[1]["role"], "system")
        self.assertIn("Tool result (file.list_dir):", messages[1]["content"])
        self.assertNotEqual(messages[1]["role"], "assistant")

    def test_extract_fallback_tool_calls_from_text_json_block(self) -> None:
        text = """```json
{"tool_calls":[{"name":"weather","arguments":{"city":"Berlin"}}]}
```"""
        calls = extract_fallback_tool_calls_from_text(
            text, allowed_tool_names=["weather"]
        )
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].name, "weather")
        self.assertEqual(calls[0].arguments.get("city"), "Berlin")

    def test_extract_fallback_tool_calls_collects_schema_shaped_json_blocks(
        self,
    ) -> None:
        text = """I'll write the files.

```json
{"path":"pyproject.toml","content":"[project]"}
```

```json
{"path":"README.md","content":"# Demo"}
```
"""
        calls = extract_fallback_tool_calls_from_text(
            text,
            allowed_tool_names=["file.write"],
        )

        self.assertEqual([call.name for call in calls], ["file.write", "file.write"])
        self.assertEqual(calls[0].arguments.get("path"), "pyproject.toml")
        self.assertEqual(calls[1].arguments.get("path"), "README.md")

    def test_extract_fallback_tool_calls_collects_inline_tool_name_tool_input_objects(
        self,
    ) -> None:
        text = (
            "I'll make the edits.\n"
            '{"tool_name":"file.write","tool_input":{"path":"pyproject.toml",'
            '"content":"[project.scripts]\\n"}}\n'
            '{"tool_name":"exec.run","tool_input":{"command":"python -m pytest -q tests"}}'
        )

        calls = extract_fallback_tool_calls_from_text(
            text,
            allowed_tool_names=["file.write", "exec.run"],
        )

        self.assertEqual([call.name for call in calls], ["file.write", "exec.run"])
        self.assertEqual(calls[0].arguments.get("path"), "pyproject.toml")
        self.assertEqual(calls[1].arguments.get("command"), "python -m pytest -q tests")

    def test_extract_fallback_tool_calls_infers_exec_run_schema_block(self) -> None:
        text = """```json
{"command":"python -m pytest -q tests","cwd":"/tmp/workspace"}
```"""
        calls = extract_fallback_tool_calls_from_text(
            text,
            allowed_tool_names=["exec.run"],
        )

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].name, "exec.run")
        self.assertEqual(calls[0].arguments.get("command"), "python -m pytest -q tests")

    def test_extract_fallback_tool_calls_repairs_eof_truncated_tool_call_json(
        self,
    ) -> None:
        text = (
            "<tool_call>\n"
            '{"name":"file.write","parameters":{"path":"tests/test_report.py",'
            '"content":"assert True\\n","CONTENT":null}\n'
            "</tool_call>"
        )

        calls = extract_fallback_tool_calls_from_text(
            text,
            model_name="MiniMax-M2.7",
            allowed_tool_names=["file.write"],
        )

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].name, "file.write")
        self.assertEqual(calls[0].arguments.get("path"), "tests/test_report.py")
        self.assertEqual(calls[0].arguments.get("content"), "assert True\n")

    def test_structured_parser_accepts_schema_shaped_json_blocks(self) -> None:
        text = """```json
{"path":"pyproject.toml","content":"[project]"}
```"""
        result = parse_structured_tool_call_envelopes(
            text,
            model_name="MiniMax-M2.7",
            allowed_tool_names=["file.write"],
        )

        self.assertEqual(len(result.calls), 1)
        self.assertEqual(result.calls[0].name, "file.write")
        self.assertEqual(result.calls[0].arguments.get("path"), "pyproject.toml")

    def test_extract_fallback_tool_calls_schema_only_accepts_plain_payload(
        self,
    ) -> None:
        text = """```json
{
  "mode": "respond",
  "confidence": 1.0,
  "reason_code": "greeting",
  "sub_intents": [],
  "rationale": "",
  "answer": "Hi there!"
}
```"""
        calls = extract_fallback_tool_calls_from_text(
            text,
            allowed_tool_names=["submit_output"],
        )
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].name, "submit_output")
        self.assertEqual(calls[0].arguments.get("mode"), "respond")

    def test_extract_fallback_tool_calls_schema_only_unwraps_decision_key(self) -> None:
        text = """```json
{
  "decision": {
    "mode": "plan",
    "confidence": 0.9,
    "reason_code": "compound_intent",
    "sub_intents": ["check_weather", "check_time"],
    "rationale": ""
  }
}
```"""
        calls = extract_fallback_tool_calls_from_text(
            text,
            allowed_tool_names=["submit_output"],
        )
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].name, "submit_output")
        self.assertEqual(calls[0].arguments.get("mode"), "plan")

    def test_extract_fallback_tool_calls_schema_only_accepts_minimax_xml(self) -> None:
        text = (
            "<minimax:tool_call>"
            '<invoke name="submit_output">'
            '<param name="mode">respond</param>'
            '<param name="confidence">1.0</param>'
            '<param name="reason_code">greeting</param>'
            '<param name="sub_intents">[]</param>'
            '<param name="rationale"></param>'
            '<param name="answer">hello</param>'
            "</invoke>"
            "</minimax:tool_call>"
        )
        calls = extract_fallback_tool_calls_from_text(
            text,
            provider_name="openai",
            model_name="MiniMax-M2.5",
            allowed_tool_names=["submit_output"],
        )
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].name, "submit_output")
        self.assertEqual(calls[0].arguments.get("mode"), "respond")

    def test_extract_openai_message_tool_calls_rejects_legacy_weather_runtime_name(
        self,
    ) -> None:
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
            message, allowed_tool_names=["weather.openmeteo.current"]
        )
        self.assertEqual(len(calls), 0)

    def test_extract_openai_message_tool_calls_strips_functions_prefix(self) -> None:
        message = {
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "functions.weather",
                        "arguments": '{"location":"San Francisco"}',
                    },
                }
            ]
        }
        calls = extract_openai_message_tool_calls(
            message, allowed_tool_names=["weather"]
        )
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].name, "weather")
        self.assertEqual(calls[0].arguments.get("location"), "San Francisco")

    def test_extract_fallback_tool_calls_from_text_rejects_legacy_weather_runtime_name(
        self,
    ) -> None:
        text = '{"name":"weather","arguments":{"location":"Canada"}}'
        calls = extract_fallback_tool_calls_from_text(
            text, allowed_tool_names=["weather.openmeteo.current"]
        )
        self.assertEqual(len(calls), 0)

    def test_extract_fallback_tool_calls_from_prefixed_text_rejects_legacy_weather_runtime_name(
        self,
    ) -> None:
        text = 'cortensor35: {"name":"weather","arguments":{"location":"Canada"}}'
        calls = extract_fallback_tool_calls_from_text(
            text, allowed_tool_names=["weather.openmeteo.current"]
        )
        self.assertEqual(len(calls), 0)

    def test_extract_fallback_tool_calls_rejects_exec_package_alias(self) -> None:
        text = """[debug-tools-v5|cortensor35] cortensor35: ```json
{
  "name": "openminion-tool-exec",
  "arguments": {
    "command": "ps"
  }
}
```"""
        calls = extract_fallback_tool_calls_from_text(
            text, allowed_tool_names=["exec.run"]
        )
        self.assertEqual(len(calls), 0)

    def test_extract_openai_message_tool_calls_resolves_file_list_dir(self) -> None:
        message = {
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "file.list_dir",
                        "arguments": '{"path":"."}',
                    },
                }
            ]
        }
        calls = extract_openai_message_tool_calls(
            message, allowed_tool_names=["file.list_dir"]
        )
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].name, "file.list_dir")
        self.assertEqual(calls[0].arguments.get("path"), ".")

    def test_extract_openai_message_tool_calls_resolves_file_wrapper_writes(
        self,
    ) -> None:
        message = {
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "file",
                        "arguments": '{"file_path":"pyproject.toml","content":"[project]"}',
                    },
                },
                {
                    "id": "call_2",
                    "type": "function",
                    "function": {
                        "name": "file",
                        "arguments": '{"file_path":"README.md","content":"# Demo"}',
                    },
                },
            ]
        }
        calls = extract_openai_message_tool_calls(
            message,
            allowed_tool_names=["file.write", "file.read", "file.list_dir"],
        )
        self.assertEqual(len(calls), 2)
        self.assertEqual([call.name for call in calls], ["file.write", "file.write"])
        self.assertEqual(calls[0].arguments.get("path"), "pyproject.toml")
        self.assertEqual(calls[0].arguments.get("file_path"), "pyproject.toml")
        self.assertEqual(calls[1].arguments.get("content"), "# Demo")

    def test_extract_openai_message_tool_calls_rejects_ambiguous_file_wrapper(
        self,
    ) -> None:
        message = {
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "file",
                        "arguments": '{"file_path":"README.md"}',
                    },
                }
            ]
        }
        calls = extract_openai_message_tool_calls(
            message,
            allowed_tool_names=["file.write", "file.read", "file.list_dir"],
        )
        self.assertEqual(calls, [])

    def test_extract_fallback_tool_calls_resolves_file_list_dir(self) -> None:
        text = '{"tool_calls":[{"name":"file.list_dir","arguments":{"path":"."}}]}'
        calls = extract_fallback_tool_calls_from_text(
            text, allowed_tool_names=["file.list_dir"]
        )
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].name, "file.list_dir")
        self.assertEqual(calls[0].arguments.get("path"), ".")

    def test_extract_fallback_tool_calls_resolves_file_read(self) -> None:
        text = '{"name":"file.read","arguments":{"file_path":"/workspace/test.txt"}}'
        calls = extract_fallback_tool_calls_from_text(
            text, allowed_tool_names=["file.read"]
        )
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].name, "file.read")

    def test_extract_fallback_tool_calls_resolves_file_find(self) -> None:
        text = '{"name":"file.find","arguments":{"pattern":"*.py"}}'
        calls = extract_fallback_tool_calls_from_text(
            text, allowed_tool_names=["file.find"]
        )
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].name, "file.find")

    def test_extract_fallback_tool_calls_rejects_legacy_file_aliases(self) -> None:
        calls = extract_fallback_tool_calls_from_text(
            '{"name":"list_files","arguments":{"path":"."}}',
            allowed_tool_names=["file.list_dir"],
        )
        self.assertEqual(len(calls), 0)
        calls = extract_fallback_tool_calls_from_text(
            '{"name":"read_file","arguments":{"file_path":"x"}}',
            allowed_tool_names=["file.read"],
        )
        self.assertEqual(len(calls), 0)
        calls = extract_fallback_tool_calls_from_text(
            '{"name":"find_files","arguments":{"pattern":"*.py"}}',
            allowed_tool_names=["file.find"],
        )
        self.assertEqual(len(calls), 0)

    def test_unknown_file_alias_returns_none(self) -> None:
        text = '{"name":"legacy.write","arguments":{"path":"/tmp/test.txt"}}'
        calls = extract_fallback_tool_calls_from_text(
            text, allowed_tool_names=["file.list_dir", "file.read"]
        )
        self.assertEqual(len(calls), 0)

    def test_file_list_dir_case_insensitive(self) -> None:
        text = '{"name":"FILE.LIST_DIR","arguments":{"path":"."}}'
        calls = extract_fallback_tool_calls_from_text(
            text, allowed_tool_names=["file.list_dir"]
        )
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].name, "file.list_dir")


def _toolspec(*, name: str, fail_with: str | None = None):
    from openminion.modules.tool.registry import ToolSpec as _RegistryToolSpec

    def handler(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
        del args, ctx
        if fail_with:
            return {"ok": False, "error": fail_with, "content": ""}
        return {"ok": True, "content": "ok"}

    return _RegistryToolSpec(
        name=name,
        args_model=dict,
        min_scope="READ_ONLY",
        handler=handler,
    )


def test_tool_result_metadata_contract_for_runtime_fallback_chain() -> None:
    from openminion.modules.tool.registry import ToolRegistry

    registry = ToolRegistry()
    registry._tools["search.tavily.search"] = _toolspec(
        name="search.tavily.search",
        fail_with="timeout from upstream",
    )
    registry._tools["search.fallback"] = _toolspec(name="search.fallback")

    context = ToolExecutionContext(
        channel="console",
        target="cli-chat",
        metadata={
            "runtime_binding_policies": {
                "runtime.web.search": {
                    "primary": "search.tavily.search",
                    "fallback_tools": ["search.fallback"],
                },
                "runtime_fallback_on": ["timeout"],
                "runtime_no_fallback_on": ["policy_denied"],
            }
        },
    )

    result = registry.execute_calls(
        [
            ProviderToolCall(
                name="web.search",
                arguments={"query": "latest news"},
                id="call-1",
                source="test",
            )
        ],
        context=context,
    ).results[0]

    assert result.ok
    assert result.data.get("model_tool_name") == "web.search"
    assert result.data.get("runtime_binding_id") == "runtime.web.search"
    assert result.data.get("runtime_tool_name") == "search.fallback"
    assert result.data.get("runtime_fallback_chain") == [
        "search.tavily.search",
        "search.fallback",
    ]
    assert result.data.get("runtime_fallback_used") is True
