import json
import io
from argparse import Namespace
from contextlib import redirect_stdout
from pathlib import Path
from runpy import run_path

import pytest

from openminion.base.version import OPENMINION_VERSION
from openminion.cli.commands.scaffold import scaffold_component


class TestScaffoldCommand:
    @staticmethod
    def _run_silenced(args: Namespace) -> int:
        with redirect_stdout(io.StringIO()):
            return scaffold_component(args)

    def test_scaffold_provider_creates_module(self, tmp_path: Path) -> None:
        args = Namespace(
            component="provider",
            name="weather",
            root=str(tmp_path),
            force=False,
            agent_id=None,
        )
        code = self._run_silenced(args)
        assert code == 0

        file_path = tmp_path / "src/openminion/providers/weather.py"
        assert file_path.exists()
        content = file_path.read_text(encoding="utf-8")
        assert "class WeatherProvider" in content
        assert 'name = "weather"' in content
        assert "openminion.modules.llm.providers.base" in content

    def test_scaffold_agent_creates_identity_bundle(self, tmp_path: Path) -> None:
        args = Namespace(
            component="agent",
            name="ops-assistant",
            root=str(tmp_path),
            force=False,
            agent_id=None,
        )
        code = self._run_silenced(args)
        assert code == 0

        agent_root = tmp_path / "agents/ops_assistant"
        assert (agent_root / "AGENT.md").exists()
        assert (agent_root / "SOUL.md").exists()
        assert (agent_root / "SKILLS/hello/SKILL.md").exists()
        assert (agent_root / "NOTES/improvements.md").exists()

    def test_scaffold_skill_with_agent_id(self, tmp_path: Path) -> None:
        args = Namespace(
            component="skill",
            name="greet",
            root=str(tmp_path),
            force=False,
            agent_id="alpha",
        )
        code = self._run_silenced(args)
        assert code == 0

        skill_root = tmp_path / "agents/alpha/SKILLS/greet"
        assert (skill_root / "SKILL.md").exists()
        assert (skill_root / "fixtures/input.json").exists()
        assert (skill_root / "fixtures/expected.txt").exists()

    def test_scaffold_plugin_creates_manifest(self, tmp_path: Path) -> None:
        args = Namespace(
            component="plugin",
            name="sanitizer",
            root=str(tmp_path),
            force=False,
            agent_id=None,
        )
        code = self._run_silenced(args)
        assert code == 0

        plugin_py = tmp_path / "src/openminion/extensions/custom/sanitizer.py"
        manifest = tmp_path / "src/openminion/extensions/custom/sanitizer.manifest.json"
        assert plugin_py.exists()
        assert manifest.exists()
        assert "openminion.services.runtime.plugins" in plugin_py.read_text(
            encoding="utf-8"
        )

        payload = json.loads(manifest.read_text(encoding="utf-8"))
        assert payload["id"] == "example.sanitizer"
        assert payload["version"] == OPENMINION_VERSION
        assert payload["trust_tier"] == "local-dev"
        assert "provenance" in payload
        assert payload["provenance"]["source"] == "local-path"

    def test_scaffold_channel_and_tool_use_current_runtime_contracts(
        self, tmp_path: Path
    ) -> None:
        channel_args = Namespace(
            component="channel",
            name="relay",
            root=str(tmp_path),
            force=False,
            agent_id=None,
        )
        tool_args = Namespace(
            component="tool",
            name="greeter",
            root=str(tmp_path),
            force=False,
            agent_id=None,
        )
        assert self._run_silenced(channel_args) == 0
        assert self._run_silenced(tool_args) == 0

        channel_path = tmp_path / "src/openminion/channels/relay.py"
        tool_path = tmp_path / "src/openminion/tools/greeter.py"
        assert "openminion.base.channel.interface" in channel_path.read_text(
            encoding="utf-8"
        )
        tool_content = tool_path.read_text(encoding="utf-8")
        assert "openminion.modules.tool" in tool_content
        assert "ToolExecutionPolicy" in tool_content
        assert "ToolExecutionResult" in tool_content
        assert "GreeterTool" in run_path(str(tool_path))

    def test_scaffold_pack_memory_creates_pack_files(self, tmp_path: Path) -> None:
        args = Namespace(
            component="pack-memory",
            name="starter",
            root=str(tmp_path),
            force=False,
            agent_id=None,
        )
        code = self._run_silenced(args)
        assert code == 0

        pack_root = tmp_path / "extensions/memory/starter"
        assert (pack_root / "README.md").exists()
        assert (pack_root / "plugin.py").exists()
        assert (pack_root / "manifest.json").exists()
        payload = json.loads((pack_root / "manifest.json").read_text())
        assert payload["version"] == OPENMINION_VERSION
        plugin_content = (pack_root / "plugin.py").read_text(encoding="utf-8")
        assert "openminion.services.runtime.plugins" in plugin_content
        assert "openminion.modules.tool" in plugin_content
        assert "register_tools" in plugin_content
        assert "PluginContext" in plugin_content
        assert "StarterMemoryLookupTool" in run_path(str(pack_root / "plugin.py"))

    def test_scaffold_pack_automation_creates_pack_files(self, tmp_path: Path) -> None:
        args = Namespace(
            component="pack-automation",
            name="cronkit",
            root=str(tmp_path),
            force=False,
            agent_id=None,
        )
        code = self._run_silenced(args)
        assert code == 0

        pack_root = tmp_path / "extensions/automation/cronkit"
        assert (pack_root / "README.md").exists()
        assert (pack_root / "plugin.py").exists()
        assert (pack_root / "manifest.json").exists()
        payload = json.loads((pack_root / "manifest.json").read_text())
        assert payload["version"] == OPENMINION_VERSION
        plugin_content = (pack_root / "plugin.py").read_text(encoding="utf-8")
        assert "class AutomationTrigger" in plugin_content
        assert "class AutomationResult" in plugin_content
        assert "openminion.services.runtime.plugins" in plugin_content

    def test_scaffold_pack_channels_chat_creates_adapter_files(
        self, tmp_path: Path
    ) -> None:
        args = Namespace(
            component="pack-channels-chat",
            name="social",
            root=str(tmp_path),
            force=False,
            agent_id=None,
        )
        code = self._run_silenced(args)
        assert code == 0

        pack_root = tmp_path / "extensions/channels/social"
        assert (pack_root / "README.md").exists()
        assert (pack_root / "manifest.json").exists()
        payload = json.loads((pack_root / "manifest.json").read_text())
        assert payload["version"] == OPENMINION_VERSION
        assert (pack_root / "factory.py").exists()
        assert (pack_root / "adapters/slack.py").exists()
        assert (pack_root / "adapters/discord.py").exists()
        assert (pack_root / "adapters/telegram.py").exists()
        assert (pack_root / "adapters/whatsapp.py").exists()
        slack_content = (pack_root / "adapters/slack.py").read_text(encoding="utf-8")
        factory_content = (pack_root / "factory.py").read_text(encoding="utf-8")
        assert "openminion.base.channel.interface" in slack_content
        assert "openminion.base.channel.interface" in factory_content
        assert "build_channels" in factory_content
        assert ".adapters.slack" in factory_content

    def test_scaffold_rejects_invalid_name(self, tmp_path: Path) -> None:
        args = Namespace(
            component="provider",
            name="../bad",
            root=str(tmp_path),
            force=False,
            agent_id=None,
        )
        with pytest.raises(RuntimeError):
            scaffold_component(args)

    def test_scaffold_refuses_overwrite_without_force(self, tmp_path: Path) -> None:
        args = Namespace(
            component="provider",
            name="weather",
            root=str(tmp_path),
            force=False,
            agent_id=None,
        )
        assert self._run_silenced(args) == 0

        with pytest.raises(RuntimeError):
            self._run_silenced(args)

    def test_scaffold_force_allows_overwrite(self, tmp_path: Path) -> None:
        args = Namespace(
            component="provider",
            name="weather",
            root=str(tmp_path),
            force=False,
            agent_id=None,
        )
        assert self._run_silenced(args) == 0

        target = tmp_path / "src/openminion/providers/weather.py"
        target.write_text("manually changed\n", encoding="utf-8")

        force_args = Namespace(
            component="provider",
            name="weather",
            root=str(tmp_path),
            force=True,
            agent_id=None,
        )
        assert self._run_silenced(force_args) == 0
        assert "WeatherProvider" in target.read_text(encoding="utf-8")
