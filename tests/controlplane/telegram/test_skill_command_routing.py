import pytest

from openminion.modules.controlplane.channels.telegram.command_aliases import (
    normalize_command_aliases,
)


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("/skill", "/skill ls"),
        ("/skill ls", "/skill ls"),
        ("/skill list", "/skill ls"),
        ("/skill ingest /path/to/SKILL.md", "/skill ingest /path/to/SKILL.md"),
        ("/skill learn /path/to/SKILL.md", "/skill ingest /path/to/SKILL.md"),
        ("/skill load /path/to/SKILL.md", "/skill ingest /path/to/SKILL.md"),
        ("/skill ingest", "/skill ingest"),
        ("/skill use my-skill", "/skill use my-skill"),
        ("/skill run my-skill arg1", "/skill use my-skill arg1"),
        ("/skill execute my-skill", "/skill use my-skill"),
        ("/skill unknown", "/skill ls"),
    ],
)
def test_skill_command_routing(command: str, expected: str) -> None:
    result = normalize_command_aliases(command, bot_username="testbot")
    assert result == expected
