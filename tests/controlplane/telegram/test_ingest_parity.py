from __future__ import annotations

import pytest

from openminion.modules.controlplane.channels.telegram.command_aliases import (
    normalize_command_aliases,
)


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("/skill ingest /path/to/SKILL.md", "/skill ingest /path/to/SKILL.md"),
        ("/skill learn /path/to/SKILL.md", "/skill ingest /path/to/SKILL.md"),
        ("/skill load /path/to/SKILL.md", "/skill ingest /path/to/SKILL.md"),
        ("/skill ingest", "/skill ingest"),
        ("/skill", "/skill ls"),
        ("/skill ls", "/skill ls"),
        ("/skill list", "/skill ls"),
        ("/skill unknown", "/skill ls"),
        ("/skill use my-skill", "/skill use my-skill"),
        ("/skill run my-skill arg1", "/skill use my-skill arg1"),
        ("/skill execute my-skill", "/skill use my-skill"),
    ],
)
def test_ingest_and_skill_alias_contract(command: str, expected: str) -> None:
    assert normalize_command_aliases(command, bot_username="testbot") == expected


@pytest.mark.parametrize(
    ("command", "expected_fragment"),
    [
        ("/skill ingest /nonexistent/path/SKILL.md", "/skill ingest"),
        ("/skill ingest /invalid/path.md", "/skill"),
    ],
)
def test_ingest_error_contract(command: str, expected_fragment: str) -> None:
    result = normalize_command_aliases(command, bot_username="testbot")
    assert expected_fragment in result
