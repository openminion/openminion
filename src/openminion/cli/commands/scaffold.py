from __future__ import annotations

import argparse
import re
from pathlib import Path

from openminion.base.version import OPENMINION_VERSION
from openminion.cli.config import resolve_cli_roots

VALID_NAME_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}$")


def scaffold_component(args) -> int:
    component = str(args.component).strip().lower()
    name = _validate_name(args.name, field="name")
    root = _resolve_root(args.root, component=component)
    force = bool(args.force)
    agent_id = _validate_optional_name(
        getattr(args, "agent_id", None), field="agent-id"
    )

    if component == "provider":
        files = _provider_files(name)
    elif component == "channel":
        files = _channel_files(name)
    elif component == "plugin":
        files = _plugin_files(name)
    elif component == "tool":
        files = _tool_files(name)
    elif component == "skill":
        files = _skill_files(name, agent_id=agent_id)
    elif component == "agent":
        files = _agent_files(name)
    elif component == "pack-memory":
        files = _pack_memory_files(name)
    elif component == "pack-automation":
        files = _pack_automation_files(name)
    elif component == "pack-channels-chat":
        files = _pack_channels_chat_files(name)
    else:
        raise RuntimeError(f"Unsupported scaffold component: {component}")

    written: list[Path] = []
    for rel_path, content in files.items():
        target = _safe_target(root, rel_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and not force:
            rel = target.relative_to(root)
            raise RuntimeError(
                f"Refusing to overwrite existing file: {rel} (use --force)"
            )
        target.write_text(content, encoding="utf-8")
        written.append(target)

    print(f"Scaffolded {component} '{name}' under {root}")
    for file_path in written:
        print(f"- {file_path.relative_to(root)}")
    return 0


def _validate_name(raw: str, *, field: str) -> str:
    value = str(raw).strip()
    if not VALID_NAME_RE.fullmatch(value):
        raise RuntimeError(
            f"Invalid {field} '{raw}'. Use letters/numbers/_/-, start with a letter, max 64 chars."
        )
    return value


def _validate_optional_name(raw: str | None, *, field: str) -> str | None:
    if raw is None:
        return None
    value = str(raw).strip()
    if not value:
        return None
    return _validate_name(value, field=field)


def _resolve_root(raw_root: str, *, component: str) -> Path:
    if raw_root:
        root = Path(raw_root).expanduser().resolve()
        if not root.exists():
            raise RuntimeError(f"Root path does not exist: {root}")
        if not root.is_dir():
            raise RuntimeError(f"Root path is not a directory: {root}")
        return root

    if component in {"agent", "skill"}:
        data_root = resolve_cli_roots(fallback_to_cwd=True).data_root
        data_root.mkdir(parents=True, exist_ok=True)
        return data_root

    root = Path.cwd().resolve()
    if not root.exists():
        raise RuntimeError(f"Root path does not exist: {root}")
    if not root.is_dir():
        raise RuntimeError(f"Root path is not a directory: {root}")
    return root


def _safe_target(root: Path, relative_path: str) -> Path:
    target = (root / relative_path).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise RuntimeError(f"Unsafe output path: {relative_path}") from exc
    return target


def _module_name(name: str) -> str:
    return name.lower().replace("-", "_")


def _class_base(name: str) -> str:
    normalized = name.replace("-", "_")
    return "".join(part.capitalize() for part in normalized.split("_") if part)


def _provider_files(name: str) -> dict[str, str]:
    module = _module_name(name)
    class_base = _class_base(name)
    return {
        f"src/openminion/providers/{module}.py": (
            "from __future__ import annotations\n"
            "\n"
            "from openminion.modules.llm.providers.base import (\n"
            "    LLMProvider,\n"
            "    ProviderRequest,\n"
            "    ProviderResponse,\n"
            ")\n"
            "\n"
            "\n"
            f"class {class_base}Provider(LLMProvider):\n"
            f'    name = "{module}"\n'
            "\n"
            "    async def generate(self, request: ProviderRequest) -> ProviderResponse:\n"
            '        user = request.user_message.strip() or "friend"\n'
            f'        text = f"Hello, {{user}}. This reply came from {class_base}Provider."\n'
            "        usage = {\n"
            '            "input_chars": len(request.user_message),\n'
            '            "output_chars": len(text),\n'
            "        }\n"
            f'        return ProviderResponse(text=text, model="{module}-v1", usage=usage)\n'
        )
    }


def _channel_files(name: str) -> dict[str, str]:
    module = _module_name(name)
    class_base = _class_base(name)
    return {
        f"src/openminion/channels/{module}.py": (
            "from __future__ import annotations\n"
            "\n"
            "from datetime import timezone\n"
            "\n"
            "from openminion.base.channel.interface import Channel\n"
            "from openminion.base.types import Message\n"
            "\n"
            "\n"
            f"class {class_base}Channel(Channel):\n"
            f'    name = "{module}"\n'
            "\n"
            "    def send(self, message: Message) -> None:\n"
            "        timestamp = message.timestamp.astimezone(timezone.utc).isoformat()\n"
            "        print(\n"
            f'            f"[{{timestamp}}] [{module}] target={{message.target}} id={{message.id}} body={{message.body}}"\n'
            "        )\n"
        )
    }


def _plugin_files(name: str) -> dict[str, str]:
    module = _module_name(name)
    class_base = _class_base(name)
    return {
        f"src/openminion/extensions/custom/{module}.py": (
            "from __future__ import annotations\n"
            "\n"
            "from openminion.base.types import AgentResponse, Message\n"
            "from openminion.services.runtime.plugins import Plugin, PluginContext\n"
            "\n"
            "\n"
            f"class {class_base}Plugin(Plugin):\n"
            f'    name = "{module}"\n'
            "\n"
            "    def on_message(self, message: Message, context: PluginContext) -> Message:\n"
            "        metadata = dict(message.metadata)\n"
            f'        metadata["{module}_inbound"] = "true"\n'
            "        return Message(\n"
            "            channel=message.channel,\n"
            "            target=message.target,\n"
            "            body=message.body,\n"
            "            metadata=metadata,\n"
            "            id=message.id,\n"
            "            timestamp=message.timestamp,\n"
            "        )\n"
            "\n"
            "    def on_response(\n"
            "        self,\n"
            "        response: AgentResponse,\n"
            "        message: Message,\n"
            "        context: PluginContext,\n"
            "    ) -> AgentResponse:\n"
            "        metadata = dict(response.metadata)\n"
            f'        metadata["{module}_outbound"] = "true"\n'
            "        return AgentResponse(\n"
            f'            text=f"{{response.text}}\\n\\n({module}-plugin footer)",\n'
            "            channel=response.channel,\n"
            "            target=response.target,\n"
            "            metadata=metadata,\n"
            "        )\n"
        ),
        f"src/openminion/extensions/custom/{module}.manifest.json": (
            "{\n"
            f'  "id": "example.{module}",\n'
            f'  "name": "{class_base} Plugin",\n'
            f'  "version": "{OPENMINION_VERSION}",\n'
            '  "description": "Scaffolded plugin manifest.",\n'
            '  "config_schema": {\n'
            '    "type": "object",\n'
            '    "properties": {\n'
            '      "enabled": {\n'
            '        "type": "boolean",\n'
            '        "default": true\n'
            "      }\n"
            "    },\n"
            '    "additionalProperties": false\n'
            "  },\n"
            '  "trust_tier": "local-dev",\n'
            '  "provenance": {\n'
            '    "source": "local-path",\n'
            '    "uri": "",\n'
            '    "publisher": "",\n'
            '    "checksum": "",\n'
            '    "verified": false\n'
            "  },\n"
            '  "requested_capabilities": [\n'
            '    "message.inbound.read",\n'
            '    "message.outbound.modify"\n'
            "  ]\n"
            "}\n"
        ),
    }


def _tool_files(name: str) -> dict[str, str]:
    module = _module_name(name)
    class_base = _class_base(name)
    return {
        f"src/openminion/tools/{module}.py": (
            "from __future__ import annotations\n"
            "\n"
            "from typing import Any, Mapping\n"
            "\n"
            "from openminion.modules.tool import (\n"
            "    Tool,\n"
            "    ToolExecutionContext,\n"
            "    ToolExecutionPolicy,\n"
            "    ToolExecutionResultV2,\n"
            ")\n"
            "\n"
            f"class {class_base}Tool(Tool):\n"
            f'    name = "{module}"\n'
            f'    description = "{class_base} tool."\n'
            "    policy = ToolExecutionPolicy(\n"
            '        required_scopes_all=("tool.execute",),\n'
            '        risk="low",\n'
            "        budget_cost=1,\n"
            "    )\n"
            "    parameters = {\n"
            '        "type": "object",\n'
            '        "properties": {\n'
            '            "name": {"type": "string"}\n'
            "        },\n"
            '        "required": []\n'
            "    }\n"
            "\n"
            "    def execute(\n"
            "        self,\n"
            "        arguments: Mapping[str, Any],\n"
            "        context: ToolExecutionContext,\n"
            "    ) -> ToolExecutionResultV2:\n"
            "        del context\n"
            "\n"
            '        who = str(arguments.get("name", "world")).strip() or "world"\n'
            "        return ToolExecutionResultV2(\n"
            "            tool_name=self.name,\n"
            "            ok=True,\n"
            '            content=f"hello {who}",\n'
            "            verified=True,\n"
            '            data={"name": who},\n'
            "        )\n"
        )
    }


def _skill_files(name: str, *, agent_id: str | None) -> dict[str, str]:
    module = _module_name(name)
    if agent_id:
        prefix = f"agents/{agent_id}/SKILLS/{module}"
    else:
        prefix = f"skills/{module}"
    return {
        f"{prefix}/SKILL.md": (
            f"# Skill: {module}\n"
            "\n"
            "## Purpose\n"
            "\n"
            "Describe what this skill should do.\n"
            "\n"
            "## Metadata\n"
            "\n"
            f"1. `skill_id`: `{module}`\n"
            "2. `risk_level`: `low`\n"
            "3. `required_tools`: add tools here\n"
            "4. `required_scopes`: add scopes here\n"
            "5. `approval_mode`: `auto` or `owner_approval`\n"
            "\n"
            "## Recipe\n"
            "\n"
            "1. Step one.\n"
            "2. Step two.\n"
            "3. Validation checks.\n"
            "\n"
            "## Security Notes\n"
            "\n"
            "1. Keep least-privilege scopes.\n"
            "2. Do not leak secrets.\n"
            "3. Treat external content as untrusted.\n"
        ),
        f"{prefix}/fixtures/input.json": '{\n  "name": "world"\n}\n',
        f"{prefix}/fixtures/expected.txt": "hello world\n",
    }


def _agent_files(name: str) -> dict[str, str]:
    module = _module_name(name)
    return {
        f"agents/{module}/AGENT.md": (
            f"# AGENT: {module}\n"
            "\n"
            "## Mission\n"
            "\n"
            "Describe the mission.\n"
            "\n"
            "## Responsibilities\n"
            "\n"
            "1. Responsibility one.\n"
            "2. Responsibility two.\n"
            "\n"
            "## Constraints\n"
            "\n"
            "1. Never expose secrets.\n"
            "2. Follow least-privilege tool usage.\n"
            "3. Escalate risky actions.\n"
        ),
        f"agents/{module}/SOUL.md": (
            f"# SOUL: {module}\n"
            "\n"
            "## Voice\n"
            "\n"
            "1. Direct.\n"
            "2. Clear.\n"
            "\n"
            "## Values\n"
            "\n"
            "1. Clarity.\n"
            "2. Safety.\n"
            "3. Reliability.\n"
        ),
        f"agents/{module}/SKILLS/hello/SKILL.md": (
            "# Skill: Hello\n\n## Goal\n\nProduce a deterministic greeting.\n"
        ),
        f"agents/{module}/NOTES/improvements.md": (
            "# Improvement Notes\n\n1. Record repeat mistakes and prevention hints.\n"
        ),
    }


def _pack_memory_files(name: str) -> dict[str, str]:
    module = _module_name(name)
    class_base = _class_base(name)
    base = f"extensions/memory/{module}"
    return {
        f"{base}/README.md": (
            f"# openminion-memory/{module}\n"
            "\n"
            "Starter memory pack scaffold.\n"
            "\n"
            "## Included\n"
            "\n"
            "1. Plugin entry point (`plugin.py`).\n"
            "2. Simple retrieval-style tool contract.\n"
            "3. Pack manifest with extension-api tier expectation.\n"
        ),
        f"{base}/plugin.py": (
            "from __future__ import annotations\n"
            "\n"
            "from typing import Any, Mapping\n"
            "\n"
            "from openminion.modules.tool import (\n"
            "    Tool,\n"
            "    ToolExecutionContext,\n"
            "    ToolExecutionPolicy,\n"
            "    ToolExecutionResultV2,\n"
            "    ToolRegistry,\n"
            ")\n"
            "from openminion.services.runtime.plugins import Plugin, PluginContext\n"
            "\n"
            "\n"
            f"class {class_base}MemoryLookupTool(Tool):\n"
            f'    name = "{module}_memory_lookup"\n'
            f'    description = "{class_base} memory lookup tool."\n'
            "    policy = ToolExecutionPolicy(\n"
            '        required_scopes_all=("tool.execute",),\n'
            '        risk="low",\n'
            "        budget_cost=1,\n"
            "    )\n"
            "    parameters = {\n"
            '        "type": "object",\n'
            '        "properties": {"query": {"type": "string"}},\n'
            '        "required": ["query"],\n'
            "    }\n"
            "\n"
            "    def execute(\n"
            "        self,\n"
            "        arguments: Mapping[str, Any],\n"
            "        context: ToolExecutionContext,\n"
            "    ) -> ToolExecutionResultV2:\n"
            "        del context\n"
            '        query = str(arguments.get("query", "")).strip()\n'
            "        if not query:\n"
            "            return ToolExecutionResultV2(\n"
            "                tool_name=self.name,\n"
            "                ok=False,\n"
            '                content="",\n'
            "                verified=False,\n"
            '                error="missing query",\n'
            "            )\n"
            "        return ToolExecutionResultV2(\n"
            "            tool_name=self.name,\n"
            "            ok=True,\n"
            "            verified=True,\n"
            '            content=f"memory placeholder for: {query}",\n'
            '            data={"query": query},\n'
            '            source="pack-memory-stub",\n'
            "        )\n"
            "\n"
            "\n"
            f"class {class_base}MemoryPack(Plugin):\n"
            f'    name = "pack.memory.{module}"\n'
            "\n"
            "    def register_tools(\n"
            "        self,\n"
            "        tools: ToolRegistry,\n"
            "        context: PluginContext,\n"
            "    ) -> None:\n"
            "        del context\n"
            f"        tools.register({class_base}MemoryLookupTool())\n"
        ),
        f"{base}/manifest.json": (
            "{\n"
            f'  "pack_id": "openminion-memory.{module}",\n'
            f'  "version": "{OPENMINION_VERSION}",\n'
            '  "entrypoint": "plugin.py",\n'
            '  "requires_api_tier": "stable",\n'
            '  "description": "Scaffolded memory pack."\n'
            "}\n"
        ),
    }


def _pack_automation_files(name: str) -> dict[str, str]:
    module = _module_name(name)
    class_base = _class_base(name)
    base = f"extensions/automation/{module}"
    return {
        f"{base}/README.md": (
            f"# openminion-automation/{module}\n"
            "\n"
            "Starter automation pack scaffold.\n"
            "\n"
            "## Included\n"
            "\n"
            "1. Plugin entry point (`plugin.py`).\n"
            "2. Local trigger/result contract sample.\n"
            "3. Pack manifest with a minimal runtime entrypoint.\n"
        ),
        f"{base}/plugin.py": (
            "from __future__ import annotations\n"
            "\n"
            "from dataclasses import dataclass, field\n"
            "from typing import Any\n"
            "\n"
            "from openminion.services.runtime.plugins import Plugin\n"
            "\n"
            "\n"
            "@dataclass(frozen=True)\n"
            "class AutomationTrigger:\n"
            "    kind: str\n"
            "    trigger_id: str\n"
            "    payload: dict[str, Any] = field(default_factory=dict)\n"
            "\n"
            "\n"
            "@dataclass(frozen=True)\n"
            "class AutomationResult:\n"
            "    ok: bool\n"
            '    message: str = ""\n'
            "    data: dict[str, Any] = field(default_factory=dict)\n"
            "\n"
            "\n"
            f"class {class_base}AutomationPack(Plugin):\n"
            f'    name = "pack.automation.{module}"\n'
            "\n"
            "    def handle_trigger(self, trigger: AutomationTrigger) -> AutomationResult:\n"
            '        if trigger.kind != "cron":\n'
            "            return AutomationResult(\n"
            "                ok=False,\n"
            '                message=f"unsupported trigger kind: {trigger.kind}",\n'
            "            )\n"
            "        return AutomationResult(\n"
            "            ok=True,\n"
            '            message="automation trigger accepted",\n'
            '            data={"trigger_id": trigger.trigger_id},\n'
            "        )\n"
        ),
        f"{base}/manifest.json": (
            "{\n"
            f'  "pack_id": "openminion-automation.{module}",\n'
            f'  "version": "{OPENMINION_VERSION}",\n'
            '  "entrypoint": "plugin.py",\n'
            '  "requires_api_tier": "stable",\n'
            '  "description": "Scaffolded automation pack."\n'
            "}\n"
        ),
    }


def _pack_channels_chat_files(name: str) -> dict[str, str]:
    module = _module_name(name)
    base = f"extensions/channels/{module}"
    return {
        f"{base}/README.md": (
            f"# openminion-channels-chat/{module}\n"
            "\n"
            "Starter chat-channel pack scaffold.\n"
            "\n"
            "## Included Adapters\n"
            "\n"
            "1. Slack\n"
            "2. Discord\n"
            "3. Telegram\n"
            "4. WhatsApp\n"
            "\n"
            "## Notes\n"
            "\n"
            "1. Adapters are transport stubs only.\n"
            "2. Wire real API clients per channel inside each adapter.\n"
            "3. Keep inbound payloads untrusted and validate signatures/auth before use.\n"
        ),
        f"{base}/manifest.json": (
            "{\n"
            f'  "pack_id": "openminion-channels-chat.{module}",\n'
            f'  "version": "{OPENMINION_VERSION}",\n'
            '  "entrypoint": "factory.py",\n'
            '  "requires_api_tier": "stable",\n'
            '  "description": "Scaffolded chat-channel pack (Slack/Discord/Telegram/WhatsApp)."\n'
            "}\n"
        ),
        f"{base}/__init__.py": (
            'from .factory import build_channels\n\n__all__ = ["build_channels"]\n'
        ),
        f"{base}/adapters/__init__.py": (
            "from .discord import DiscordChannel\n"
            "from .slack import SlackChannel\n"
            "from .telegram import TelegramChannel\n"
            "from .whatsapp import WhatsAppChannel\n"
            "\n"
            '__all__ = ["SlackChannel", "DiscordChannel", "TelegramChannel", "WhatsAppChannel"]\n'
        ),
        f"{base}/factory.py": (
            "from __future__ import annotations\n"
            "\n"
            "from openminion.base.channel.interface import Channel\n"
            "\n"
            "from .adapters.discord import DiscordChannel\n"
            "from .adapters.slack import SlackChannel\n"
            "from .adapters.telegram import TelegramChannel\n"
            "from .adapters.whatsapp import WhatsAppChannel\n"
            "\n"
            "\n"
            "def build_channels() -> tuple[Channel, ...]:\n"
            "    return (\n"
            "        SlackChannel(),\n"
            "        DiscordChannel(),\n"
            "        TelegramChannel(),\n"
            "        WhatsAppChannel(),\n"
            "    )\n"
        ),
        f"{base}/adapters/slack.py": _chat_channel_adapter_source(
            class_name="SlackChannel",
            channel_name="slack",
            default_target="slack-channel",
        ),
        f"{base}/adapters/discord.py": _chat_channel_adapter_source(
            class_name="DiscordChannel",
            channel_name="discord",
            default_target="discord-channel",
        ),
        f"{base}/adapters/telegram.py": _chat_channel_adapter_source(
            class_name="TelegramChannel",
            channel_name="telegram",
            default_target="telegram-chat",
        ),
        f"{base}/adapters/whatsapp.py": _chat_channel_adapter_source(
            class_name="WhatsAppChannel",
            channel_name="whatsapp",
            default_target="whatsapp-chat",
        ),
    }


def _chat_channel_adapter_source(
    *, class_name: str, channel_name: str, default_target: str
) -> str:
    return (
        "from __future__ import annotations\n"
        "\n"
        "from datetime import timezone\n"
        "from typing import Any, Mapping\n"
        "\n"
        "from openminion.base.channel.interface import Channel\n"
        "from openminion.base.types import Message\n"
        "\n"
        "\n"
        f"class {class_name}(Channel):\n"
        f'    name = "{channel_name}"\n'
        "\n"
        "    def send(self, message: Message) -> None:\n"
        "        timestamp = message.timestamp.astimezone(timezone.utc).isoformat()\n"
        "        print(\n"
        f'            f"[{{timestamp}}] [{channel_name}] outbound target={{message.target}} id={{message.id}} body={{message.body}}"\n'
        "        )\n"
        "\n"
        "    def parse_inbound(self, payload: Mapping[str, Any]) -> Message:\n"
        '        text = str(payload.get("text", "")).strip()\n'
        f'        target = str(payload.get("target", "")).strip() or "{default_target}"\n'
        '        user_id = str(payload.get("user_id", "unknown")).strip() or "unknown"\n'
        "        return Message(\n"
        f'            channel="{channel_name}",\n'
        "            target=target,\n"
        "            body=text,\n"
        "            metadata={\n"
        '                "origin": "channel",\n'
        f'                "channel_adapter": "{channel_name}",\n'
        '                "sender_id": user_id,\n'
        '                "untrusted_input": "true",\n'
        "            },\n"
        "        )\n"
    )


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    scaffold = subparsers.add_parser(
        "scaffold", help="Generate starter files for extension components"
    )
    scaffold.add_argument(
        "component",
        choices=[
            "provider",
            "channel",
            "plugin",
            "tool",
            "skill",
            "agent",
            "pack-memory",
            "pack-automation",
            "pack-channels-chat",
        ],
        help="Component type to scaffold",
    )
    scaffold.add_argument("name", help="Component name (letters/numbers/_/-)")
    scaffold.add_argument(
        "--root",
        default="",
        help=(
            "Project root directory where files will be created "
            "(default: current directory; for agent/skill, uses OPENMINION_DATA_ROOT)"
        ),
    )
    scaffold.add_argument(
        "--agent-id",
        default=None,
        help="Optional agent id for skill scaffolds (writes to agents/<agent-id>/SKILLS/...)",
    )
    scaffold.add_argument(
        "--force", action="store_true", help="Overwrite existing files"
    )
    scaffold.set_defaults(handler=scaffold_component, needs_app=False)
