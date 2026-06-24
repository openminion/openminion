import unittest

from openminion.modules.context.render.renderers import (
    render_anthropic,
    render_openai,
    render_openai_with_tools,
)
from openminion.modules.context.schemas import (
    ContextPack,
    RenderMessage,
    TokenBudgetReport,
)


def _minimal_pack(messages: list[RenderMessage]) -> ContextPack:
    return ContextPack(
        session_id="sess-1",
        agent_id="agent-1",
        purpose="act",
        messages=messages,
        profile_version="prof:v1",
        render_version="rend:v1",
        slice_version="slice:v1",
        pack_version="pack-hash-abc",
        pack_hash="pack-hash-abc",
        token_budget_report=TokenBudgetReport(
            total_cap_tokens=1000,
            total_used_tokens=100,
        ),
    )


class OpenAIRendererTests(unittest.TestCase):
    def test_system_and_user_message_pass_through(self) -> None:
        pack = _minimal_pack(
            [
                RenderMessage(role="system", content="Stay on policy"),
                RenderMessage(role="user", content="Hello"),
            ]
        )
        result = render_openai(pack, model="gpt-4o")
        self.assertEqual(result["model"], "gpt-4o")
        self.assertEqual(len(result["messages"]), 2)
        self.assertEqual(result["messages"][0]["role"], "system")
        self.assertEqual(result["messages"][1]["role"], "user")

    def test_developer_role_mapped_to_system(self) -> None:
        pack = _minimal_pack(
            [
                RenderMessage(role="developer", content="Dev instructions"),
                RenderMessage(role="user", content="Hello"),
            ]
        )
        result = render_openai(pack, model="gpt-4o-mini")
        self.assertEqual(result["messages"][0]["role"], "system")
        self.assertEqual(result["messages"][0]["content"], "Dev instructions")

    def test_assistant_role_preserved(self) -> None:
        pack = _minimal_pack(
            [
                RenderMessage(role="system", content="System"),
                RenderMessage(role="user", content="Hi"),
                RenderMessage(role="assistant", content="Hello back"),
                RenderMessage(role="user", content="Thanks"),
            ]
        )
        result = render_openai(pack, model="gpt-4o")
        roles = [m["role"] for m in result["messages"]]
        self.assertEqual(roles, ["system", "user", "assistant", "user"])

    def test_dict_input_compat(self) -> None:
        payload = {
            "messages": [
                {"role": "system", "content": "Policy"},
                {"role": "user", "content": "Query"},
            ]
        }
        result = render_openai(payload, model="gpt-4o")
        self.assertEqual(len(result["messages"]), 2)

    def test_tool_role_passes_through(self) -> None:
        pack = _minimal_pack(
            [
                RenderMessage(role="system", content="System"),
                RenderMessage(role="tool", content="result_data"),
                RenderMessage(role="user", content="Apply result"),
            ]
        )
        result = render_openai(pack, model="gpt-4o")
        roles = [m["role"] for m in result["messages"]]
        self.assertIn("tool", roles)

    def test_with_tools_payload(self) -> None:
        pack = _minimal_pack(
            [RenderMessage(role="user", content="What is the weather?")]
        )
        tools = [
            {
                "type": "function",
                "function": {"name": "weather.openmeteo.current", "parameters": {}},
            }
        ]
        result = render_openai_with_tools(
            pack, model="gpt-4o", tools=tools, tool_choice="auto"
        )
        self.assertIn("tools", result)
        self.assertEqual(result["tool_choice"], "auto")
        self.assertEqual(
            result["tools"][0]["function"]["name"], "weather.openmeteo.current"
        )

    def test_no_tools_omits_tools_key(self) -> None:
        pack = _minimal_pack([RenderMessage(role="user", content="Hello")])
        result = render_openai_with_tools(pack, model="gpt-4o")
        self.assertNotIn("tools", result)


class AnthropicRendererTests(unittest.TestCase):
    def test_system_block_extracted(self) -> None:
        pack = _minimal_pack(
            [
                RenderMessage(role="system", content="Identity text"),
                RenderMessage(role="user", content="Hello"),
            ]
        )
        result = render_anthropic(pack, model="claude-3-5-sonnet-latest")
        self.assertEqual(result["system"], "Identity text")
        self.assertEqual(result["model"], "claude-3-5-sonnet-latest")

    def test_user_and_assistant_in_messages(self) -> None:
        pack = _minimal_pack(
            [
                RenderMessage(role="system", content="System"),
                RenderMessage(role="user", content="Hi"),
                RenderMessage(role="assistant", content="Hello"),
                RenderMessage(role="user", content="Thanks"),
            ]
        )
        result = render_anthropic(pack, model="claude-3-5-haiku-latest")
        roles = [m["role"] for m in result["messages"]]
        self.assertEqual(roles, ["user", "assistant", "user"])

    def test_anthropic_content_block_format(self) -> None:
        pack = _minimal_pack(
            [
                RenderMessage(role="system", content="System"),
                RenderMessage(role="user", content="Hello"),
            ]
        )
        result = render_anthropic(pack, model="claude-3-opus-latest")
        user_msg = result["messages"][0]
        self.assertEqual(user_msg["content"][0]["type"], "text")
        self.assertEqual(user_msg["content"][0]["text"], "Hello")

    def test_developer_role_merged_into_system(self) -> None:
        pack = _minimal_pack(
            [
                RenderMessage(role="system", content="Guardrails"),
                RenderMessage(role="developer", content="Dev policy"),
                RenderMessage(role="user", content="Query"),
            ]
        )
        result = render_anthropic(pack, model="claude-3-5-sonnet-latest")
        self.assertIn("Guardrails", result["system"])
        self.assertIn("Dev policy", result["system"])
        self.assertEqual(len(result["messages"]), 1)  # only user

    def test_system_not_role_lost_in_messages(self) -> None:
        pack = _minimal_pack(
            [
                RenderMessage(role="system", content="System only"),
                RenderMessage(role="user", content="Ask"),
            ]
        )
        result = render_anthropic(pack, model="claude-3-5-sonnet-latest")
        message_roles = [m["role"] for m in result["messages"]]
        self.assertNotIn("system", message_roles)

    def test_dict_input_compat(self) -> None:
        payload = {
            "messages": [
                {"role": "system", "content": "Policy"},
                {"role": "user", "content": "Query"},
            ]
        }
        result = render_anthropic(payload, model="claude-3-5-sonnet-latest")
        self.assertEqual(result["system"], "Policy")
        self.assertEqual(len(result["messages"]), 1)
