from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from tests._csc_fixtures import _csc_install_default_agent


def _message_ref() -> dict[str, str]:
    return {
        "channel": "discord",
        "conversation_id": "conv-1",
        "message_id": "msg-1",
    }


class _FakeReactionAdapter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str, str, str]] = []

    def react_add(self, message, emoji) -> None:
        self.calls.append(
            (
                "add",
                message.channel,
                message.conversation_id,
                message.message_id,
                emoji,
            )
        )


class TestReactionsConfig(unittest.TestCase):
    def test_reactions_enabled_in_runtime_config(self):
        from openminion.base.config import RuntimeConfig

        config = RuntimeConfig()
        self.assertTrue(hasattr(config, "reactions_enabled"))
        self.assertTrue(config.reactions_enabled)

    def test_reactions_default_policy_in_runtime_config(self):
        from openminion.base.config import RuntimeConfig

        config = RuntimeConfig()
        self.assertTrue(hasattr(config, "reactions_default_policy"))
        self.assertIn(config.reactions_default_policy, {"allow", "deny", "confirm"})

    def test_reactions_config_in_runtime_config_dict(self):
        from openminion.base.config import RuntimeConfig

        config = RuntimeConfig()
        config_dict = config.__dict__
        self.assertIn("reactions_enabled", config_dict)
        self.assertIn("reactions_default_policy", config_dict)


class TestReactionsDebugInfo(unittest.TestCase):
    def test_get_reactions_debug_info_returns_dict(self):
        from openminion.cli.chat.commands import _get_reactions_debug_info
        from openminion.base.config import OpenMinionConfig

        info = _get_reactions_debug_info(config=OpenMinionConfig())
        self.assertIn("enabled", info)
        self.assertIn("plugin_installed", info)
        self.assertIn("available", info)
        self.assertIn("default_policy", info)

    def test_get_reactions_debug_info_with_disabled_config(self):
        from openminion.cli.chat.commands import _get_reactions_debug_info
        from openminion.base.config import OpenMinionConfig

        config = OpenMinionConfig()
        _csc_install_default_agent(config)  # type: ignore[attr-defined]
        config.runtime.reactions_enabled = False

        info = _get_reactions_debug_info(config=config)
        self.assertFalse(info.get("enabled"))
        self.assertFalse(info.get("available"))


class TestReactionsToolInventory(unittest.TestCase):
    def test_reactions_tools_present_in_runtime_inventory_when_enabled(self):
        from openminion.base.config import RuntimeConfig
        from openminion.modules.tool import build_default_tool_registry

        registry = build_default_tool_registry(config=RuntimeConfig())
        names = {spec.name for spec in registry.provider_specs()}
        self.assertIn("reactions.set", names)
        self.assertIn("reactions.list", names)

    def test_reactions_tools_hidden_from_runtime_inventory_when_disabled(self):
        from openminion.base.config import RuntimeConfig
        from openminion.modules.tool import build_default_tool_registry

        config = RuntimeConfig()
        config.reactions_enabled = False

        registry = build_default_tool_registry(config=config)
        names = {spec.name for spec in registry.provider_specs()}
        self.assertNotIn("reactions.set", names)
        self.assertNotIn("reactions.list", names)

    def test_reactions_tools_are_not_model_facing(self):
        from openminion.modules.tool import build_default_tool_registry

        registry = build_default_tool_registry()
        names = {spec.name for spec in registry.model_provider_specs()}
        self.assertNotIn("reactions.set", names)
        self.assertNotIn("reactions.list", names)


class TestReactionsPolicyBehavior(unittest.TestCase):
    def _execute_reaction_set(
        self,
        *,
        default_policy: str,
    ) -> tuple[dict[str, object], list[tuple[str, str, str, str, str]]]:
        from openminion.base.config import RuntimeConfig
        from openminion.modules.brain.adapters.tool import ToolAdapter
        from openminion.tools.reaction.plugin import (
            clear_channel_adapters,
            register_channel_adapter,
        )

        config = RuntimeConfig()
        config.reactions_enabled = True
        config.reactions_default_policy = default_policy

        reaction_adapter = _FakeReactionAdapter()
        register_channel_adapter("discord", reaction_adapter)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                adapter = ToolAdapter(
                    workspace_root=Path(tmp),
                    runtime_config=config,
                )
                result = adapter.execute(
                    command={
                        "tool_name": "reactions.set",
                        "args": {"message": _message_ref(), "emoji": "✅"},
                    },
                    session_id="s1",
                    trace_id="t1",
                )
        finally:
            clear_channel_adapters()
        return result, reaction_adapter.calls

    def test_reactions_default_policy_allow_allows_write(self):
        result, calls = self._execute_reaction_set(default_policy="allow")

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["outputs"]["applied"]["action"], "added")
        self.assertEqual(calls, [("add", "discord", "conv-1", "msg-1", "✅")])

    def test_reactions_default_policy_deny_blocks_write(self):
        result, calls = self._execute_reaction_set(default_policy="deny")

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error"]["code"], "POLICY_DENIED")
        self.assertEqual(calls, [])

    def test_reactions_default_policy_confirm_requires_confirmation(self):
        result, calls = self._execute_reaction_set(default_policy="confirm")

        self.assertEqual(result["status"], "needs_user")
        self.assertEqual(result["error"]["code"], "CONFIRM_REQUIRED")
        self.assertEqual(calls, [])
