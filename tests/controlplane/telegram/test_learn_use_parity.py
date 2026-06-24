from __future__ import annotations

import pytest

from openminion.modules.controlplane.channels.telegram.command_aliases import (
    normalize_command_aliases,
)


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        (
            "/skill learn api-account-create-post-share",
            "/skill ingest api-account-create-post-share",
        ),
        (
            "/skill run api-account-create-post-share",
            "/skill use api-account-create-post-share",
        ),
        (
            "/skill execute api-account-create-post-share",
            "/skill use api-account-create-post-share",
        ),
        ("/skill ls", "/skill ls"),
        ("/skill use", "/skill use"),
        ("/skill ingest", "/skill ingest"),
        ("/skill", "/skill ls"),
        ("/skill unknown", "/skill ls"),
    ],
)
def test_learn_use_alias_contract(command: str, expected: str) -> None:
    assert normalize_command_aliases(command, bot_username="testbot") == expected


@pytest.mark.parametrize(
    ("command", "expected_prefix"),
    [
        ("/skill ingest /path/to/skill/SKILL.md", "/skill ingest"),
        ("/skill learn another-skill", "/skill ingest another-skill"),
        ("/skill use my-skill", "/skill use my-skill"),
        ("/skill run my-skill arg1", "/skill use my-skill arg1"),
        ("/skill execute my-skill", "/skill use my-skill"),
        ("/skill use nonexistent-skill-xyz", "/skill use"),
        ("/skill ingest /nonexistent/path/SKILL.md", "/skill ingest"),
    ],
)
def test_learn_use_command_chain_prefixes(command: str, expected_prefix: str) -> None:
    result = normalize_command_aliases(command, bot_username="testbot")
    assert result.startswith(expected_prefix)


def test_skill_ingest_then_use_command_sequence() -> None:
    ingest_cmd = normalize_command_aliases(
        "/skill ingest /path/to/SKILL.md", bot_username="testbot"
    )
    use_cmd = normalize_command_aliases(
        "/skill use api-account-create-post-share", bot_username="testbot"
    )
    assert "/skill ingest" in ingest_cmd
    assert "/skill use" in use_cmd
    assert "api-account-create-post-share" in use_cmd


def test_learn_then_use_chain_is_deterministic() -> None:
    learn_result = normalize_command_aliases(
        "/skill learn test-skill", bot_username="testbot"
    )
    use_result = normalize_command_aliases(
        "/skill use test-skill", bot_username="testbot"
    )
    assert "/skill ingest" in learn_result
    assert "/skill use" in use_result
    assert "test-skill" in learn_result
    assert "test-skill" in use_result
