from pathlib import Path

from openminion.modules.controlplane.channels.slack.command_aliases import (
    normalize_command_text,
    normalize_slash_command_text,
)


def test_slack_aliases_use_profile_and_session_vocabulary() -> None:
    assert normalize_command_text("agent use minimax-m2-5") == "/profile use minimax-m2-5"
    assert normalize_command_text("new") == "/session new"
    assert normalize_slash_command_text("/openminion", "status") == "/status"


def test_slack_does_not_define_parallel_command_registry() -> None:
    source_root = Path("src/openminion/modules/controlplane/channels/slack")
    contents = "\n".join(path.read_text() for path in source_root.glob("*.py"))

    assert "class CommandRegistry" not in contents
    assert "SlackCommandRegistry" not in contents
