from __future__ import annotations

from openminion.cli.commands.channel import TELEGRAM_BOT_COMMANDS
from openminion.modules.controlplane.channels.telegram.command_aliases import (
    normalize_command_aliases,
)
from openminion.modules.controlplane.commands.registry import CommandRegistry
from openminion.modules.controlplane.runtime.parser import SlashCommandParser
from openminion.modules.controlplane.runtime.store import InMemoryControlPlaneStore


def test_telegram_menu_commands_resolve_to_registered_controlplane_commands() -> None:
    registry = CommandRegistry(store=InMemoryControlPlaneStore())
    parser = SlashCommandParser()

    for item in TELEGRAM_BOT_COMMANDS:
        command_text = "/" + item["command"]
        normalized = normalize_command_aliases(command_text, bot_username="mybot")
        parsed = parser.parse(normalized)
        assert parsed is not None, command_text
        assert registry.get_command_spec(parsed.canonical) is not None, normalized


def test_telegram_profile_and_session_aliases_preserve_primary_vocabulary() -> None:
    assert normalize_command_aliases("/agent minimax-m2-5", bot_username="mybot") == (
        "/profile use minimax-m2-5"
    )
    assert normalize_command_aliases("/profile list", bot_username="mybot") == (
        "/profile ls"
    )
    assert normalize_command_aliases("/profile current", bot_username="mybot") == (
        "/profile"
    )
    assert normalize_command_aliases("/new", bot_username="mybot") == "/session new"
    assert normalize_command_aliases("/status", bot_username="mybot") == "/status"
