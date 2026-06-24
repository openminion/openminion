import json
import io
import tempfile
import unittest
from argparse import Namespace
from contextlib import redirect_stdout
from pathlib import Path

from openminion.cli.commands.scaffold import scaffold_component


class ScaffoldCommandTests(unittest.TestCase):
    @staticmethod
    def _run_silenced(args: Namespace) -> int:
        with redirect_stdout(io.StringIO()):
            return scaffold_component(args)

    def test_scaffold_provider_creates_module(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            args = Namespace(
                component="provider",
                name="weather",
                root=tmp,
                force=False,
                agent_id=None,
            )
            code = self._run_silenced(args)
            self.assertEqual(code, 0)

            file_path = Path(tmp) / "src/openminion/providers/weather.py"
            self.assertTrue(file_path.exists())
            content = file_path.read_text(encoding="utf-8")
            self.assertIn("class WeatherProvider", content)
            self.assertIn('name = "weather"', content)
            self.assertIn("openminion.modules.llm.providers.base", content)

    def test_scaffold_agent_creates_identity_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            args = Namespace(
                component="agent",
                name="ops-assistant",
                root=tmp,
                force=False,
                agent_id=None,
            )
            code = self._run_silenced(args)
            self.assertEqual(code, 0)

            agent_root = Path(tmp) / "agents/ops_assistant"
            self.assertTrue((agent_root / "AGENT.md").exists())
            self.assertTrue((agent_root / "SOUL.md").exists())
            self.assertTrue((agent_root / "SKILLS/hello/SKILL.md").exists())
            self.assertTrue((agent_root / "NOTES/improvements.md").exists())

    def test_scaffold_skill_with_agent_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            args = Namespace(
                component="skill",
                name="greet",
                root=tmp,
                force=False,
                agent_id="alpha",
            )
            code = self._run_silenced(args)
            self.assertEqual(code, 0)

            skill_root = Path(tmp) / "agents/alpha/SKILLS/greet"
            self.assertTrue((skill_root / "SKILL.md").exists())
            self.assertTrue((skill_root / "fixtures/input.json").exists())
            self.assertTrue((skill_root / "fixtures/expected.txt").exists())

    def test_scaffold_plugin_creates_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            args = Namespace(
                component="plugin",
                name="sanitizer",
                root=tmp,
                force=False,
                agent_id=None,
            )
            code = self._run_silenced(args)
            self.assertEqual(code, 0)

            plugin_py = Path(tmp) / "src/openminion/extensions/custom/sanitizer.py"
            manifest = (
                Path(tmp) / "src/openminion/extensions/custom/sanitizer.manifest.json"
            )
            self.assertTrue(plugin_py.exists())
            self.assertTrue(manifest.exists())
            self.assertIn(
                "openminion.services.runtime.plugins",
                plugin_py.read_text(encoding="utf-8"),
            )

            payload = json.loads(manifest.read_text(encoding="utf-8"))
            self.assertEqual(payload["id"], "example.sanitizer")
            self.assertEqual(payload["trust_tier"], "local-dev")
            self.assertIn("provenance", payload)
            self.assertEqual(payload["provenance"]["source"], "local-path")

    def test_scaffold_channel_and_tool_use_current_runtime_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            channel_args = Namespace(
                component="channel",
                name="relay",
                root=tmp,
                force=False,
                agent_id=None,
            )
            tool_args = Namespace(
                component="tool",
                name="greeter",
                root=tmp,
                force=False,
                agent_id=None,
            )
            self.assertEqual(self._run_silenced(channel_args), 0)
            self.assertEqual(self._run_silenced(tool_args), 0)

            channel_path = Path(tmp) / "src/openminion/channels/relay.py"
            tool_path = Path(tmp) / "src/openminion/tools/greeter.py"
            self.assertIn(
                "openminion.base.channel.base",
                channel_path.read_text(encoding="utf-8"),
            )
            tool_content = tool_path.read_text(encoding="utf-8")
            self.assertIn("openminion.modules.tool", tool_content)
            self.assertIn("ToolExecutionPolicy", tool_content)
            self.assertIn("ToolExecutionResultV2", tool_content)

    def test_scaffold_pack_memory_creates_pack_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            args = Namespace(
                component="pack-memory",
                name="starter",
                root=tmp,
                force=False,
                agent_id=None,
            )
            code = self._run_silenced(args)
            self.assertEqual(code, 0)

            pack_root = Path(tmp) / "extensions/memory/starter"
            self.assertTrue((pack_root / "README.md").exists())
            self.assertTrue((pack_root / "plugin.py").exists())
            self.assertTrue((pack_root / "manifest.json").exists())
            plugin_content = (pack_root / "plugin.py").read_text(encoding="utf-8")
            self.assertIn("openminion.services.runtime.plugins", plugin_content)
            self.assertIn("openminion.modules.tool", plugin_content)
            self.assertIn("register_tools", plugin_content)
            self.assertIn("PluginContext", plugin_content)

    def test_scaffold_pack_automation_creates_pack_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            args = Namespace(
                component="pack-automation",
                name="cronkit",
                root=tmp,
                force=False,
                agent_id=None,
            )
            code = self._run_silenced(args)
            self.assertEqual(code, 0)

            pack_root = Path(tmp) / "extensions/automation/cronkit"
            self.assertTrue((pack_root / "README.md").exists())
            self.assertTrue((pack_root / "plugin.py").exists())
            self.assertTrue((pack_root / "manifest.json").exists())
            plugin_content = (pack_root / "plugin.py").read_text(encoding="utf-8")
            self.assertIn("class AutomationTrigger", plugin_content)
            self.assertIn("class AutomationResult", plugin_content)
            self.assertIn("openminion.services.runtime.plugins", plugin_content)

    def test_scaffold_pack_channels_chat_creates_adapter_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            args = Namespace(
                component="pack-channels-chat",
                name="social",
                root=tmp,
                force=False,
                agent_id=None,
            )
            code = self._run_silenced(args)
            self.assertEqual(code, 0)

            pack_root = Path(tmp) / "extensions/channels/social"
            self.assertTrue((pack_root / "README.md").exists())
            self.assertTrue((pack_root / "manifest.json").exists())
            self.assertTrue((pack_root / "factory.py").exists())
            self.assertTrue((pack_root / "adapters/slack.py").exists())
            self.assertTrue((pack_root / "adapters/discord.py").exists())
            self.assertTrue((pack_root / "adapters/telegram.py").exists())
            self.assertTrue((pack_root / "adapters/whatsapp.py").exists())
            slack_content = (pack_root / "adapters/slack.py").read_text(
                encoding="utf-8"
            )
            factory_content = (pack_root / "factory.py").read_text(encoding="utf-8")
            self.assertIn("openminion.base.channel.base", slack_content)
            self.assertIn("openminion.base.channel.base", factory_content)
            self.assertIn("build_channels", factory_content)
            self.assertIn(".adapters.slack", factory_content)

    def test_scaffold_rejects_invalid_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            args = Namespace(
                component="provider",
                name="../bad",
                root=tmp,
                force=False,
                agent_id=None,
            )
            with self.assertRaises(RuntimeError):
                scaffold_component(args)

    def test_scaffold_refuses_overwrite_without_force(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            args = Namespace(
                component="provider",
                name="weather",
                root=tmp,
                force=False,
                agent_id=None,
            )
            self.assertEqual(self._run_silenced(args), 0)

            with self.assertRaises(RuntimeError):
                self._run_silenced(args)

    def test_scaffold_force_allows_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            args = Namespace(
                component="provider",
                name="weather",
                root=tmp,
                force=False,
                agent_id=None,
            )
            self.assertEqual(self._run_silenced(args), 0)

            target = Path(tmp) / "src/openminion/providers/weather.py"
            target.write_text("manually changed\n", encoding="utf-8")

            force_args = Namespace(
                component="provider",
                name="weather",
                root=tmp,
                force=True,
                agent_id=None,
            )
            self.assertEqual(self._run_silenced(force_args), 0)
            self.assertIn("WeatherProvider", target.read_text(encoding="utf-8"))
