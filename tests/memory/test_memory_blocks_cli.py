from __future__ import annotations

import json

import pytest

from openminion.cli.commands import memory as memory_cmd
from openminion.cli.parser.base import build_parser
from sophiagraph import SophiaGraphMemoryStore


@pytest.fixture
def store(monkeypatch):
    store = SophiaGraphMemoryStore()
    memory_cmd.set_store_factory(lambda args: store)
    yield store
    memory_cmd.reset_store_factory()


@pytest.fixture
def parser():
    return build_parser()


def test_all_four_subcommands_exist(parser) -> None:
    for argv in (
        ["memory", "blocks", "list", "--agent-id", "alpha"],
        [
            "memory",
            "blocks",
            "pin",
            "agent_identity",
            "You are an assistant.",
            "--agent-id",
            "alpha",
        ],
        [
            "memory",
            "blocks",
            "update",
            "blk-1",
            "new content",
            "--agent-id",
            "alpha",
        ],
        ["memory", "blocks", "unpin", "blk-1", "--agent-id", "alpha"],
    ):
        args = parser.parse_args(argv)
        assert args.command == "memory"
        assert args.memory_command == "blocks"
        assert args.blocks_command == argv[2]


def test_pin_identity_creates_read_only_block(store, parser, capsys) -> None:
    args = parser.parse_args(
        [
            "memory",
            "blocks",
            "pin",
            "agent_identity",
            "You are a focused assistant.",
            "--agent-id",
            "alpha",
        ]
    )
    rc = memory_cmd.run_memory_cli_bridge(args)
    out = capsys.readouterr().out
    assert rc == 0
    payload = json.loads(out)
    assert payload["ok"] is True
    assert payload["block"]["class_name"] == "agent_identity"
    assert payload["block"]["mode"] == "read_only"
    listed = store.list_memory_blocks()
    assert len(listed) == 1
    assert listed[0].class_name == "agent_identity"


def test_pin_mission_creates_pinned_block(store, parser, capsys) -> None:
    args = parser.parse_args(
        [
            "memory",
            "blocks",
            "pin",
            "active_mission",
            "Investigate the failing tracker.",
            "--agent-id",
            "alpha",
        ]
    )
    rc = memory_cmd.run_memory_cli_bridge(args)
    assert rc == 0
    capsys.readouterr()
    listed = store.list_memory_blocks()
    assert listed[0].mode == "pinned"


def test_pin_rejects_non_eligible_class(store, parser, capsys) -> None:
    args = parser.parse_args(
        [
            "memory",
            "blocks",
            "pin",
            "project_config",
            "anything",
            "--agent-id",
            "alpha",
        ]
    )
    rc = memory_cmd.run_memory_cli_bridge(args)
    err = capsys.readouterr().err
    assert rc == 2
    payload = json.loads(err)
    assert payload["ok"] is False
    assert payload["code"] == "MEMORY_BLOCK_CLASS_NOT_ELIGIBLE"
    assert store.list_memory_blocks() == []


def test_pin_rejects_deferred_mode(store, parser, capsys) -> None:
    args = parser.parse_args(
        [
            "memory",
            "blocks",
            "pin",
            "active_mission",
            "Mission text",
            "--mode",
            "shared",
            "--agent-id",
            "alpha",
        ]
    )
    rc = memory_cmd.run_memory_cli_bridge(args)
    err = capsys.readouterr().err
    assert rc == 2
    payload = json.loads(err)
    assert payload["ok"] is False
    assert payload["code"] == "MEMORY_BLOCK_MODE_NOT_YET_SUPPORTED"
    assert store.list_memory_blocks() == []


def test_pin_requires_namespace(store, parser) -> None:
    args = parser.parse_args(
        [
            "memory",
            "blocks",
            "pin",
            "agent_identity",
            "Some text",
        ]
    )
    with pytest.raises(SystemExit):
        memory_cmd.run_memory_cli_bridge(args)


def test_list_returns_existing_blocks(store, parser, capsys) -> None:
    parser.parse_args  # silence unused warning
    args_pin = parser.parse_args(
        [
            "memory",
            "blocks",
            "pin",
            "agent_identity",
            "Identity content.",
            "--agent-id",
            "alpha",
        ]
    )
    memory_cmd.run_memory_cli_bridge(args_pin)
    capsys.readouterr()
    args_list = parser.parse_args(["memory", "blocks", "list", "--agent-id", "alpha"])
    rc = memory_cmd.run_memory_cli_bridge(args_list)
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["count"] == 1
    assert payload["blocks"][0]["class_name"] == "agent_identity"


def test_list_namespace_isolation(store, parser, capsys) -> None:
    memory_cmd.run_memory_cli_bridge(
        parser.parse_args(
            [
                "memory",
                "blocks",
                "pin",
                "active_mission",
                "Mission A",
                "--agent-id",
                "alpha",
            ]
        )
    )
    memory_cmd.run_memory_cli_bridge(
        parser.parse_args(
            [
                "memory",
                "blocks",
                "pin",
                "active_mission",
                "Mission B",
                "--agent-id",
                "beta",
            ]
        )
    )
    capsys.readouterr()
    memory_cmd.run_memory_cli_bridge(
        parser.parse_args(["memory", "blocks", "list", "--agent-id", "alpha"])
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["count"] == 1
    assert payload["blocks"][0]["owner_namespace"] == {"agent_id": "alpha"}


def test_update_pinned_succeeds(store, parser, capsys) -> None:
    memory_cmd.run_memory_cli_bridge(
        parser.parse_args(
            [
                "memory",
                "blocks",
                "pin",
                "active_mission",
                "Investigate failing tracker.",
                "--block-id",
                "blk-mission",
                "--agent-id",
                "alpha",
            ]
        )
    )
    capsys.readouterr()
    rc = memory_cmd.run_memory_cli_bridge(
        parser.parse_args(
            [
                "memory",
                "blocks",
                "update",
                "blk-mission",
                "Investigate flake in test_x.",
                "--actor",
                "alice",
            ]
        )
    )
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["block"]["content"] == "Investigate flake in test_x."
    assert payload["block"]["last_updated_by"] == "alice"


def test_update_missing_block_returns_error(store, parser, capsys) -> None:
    rc = memory_cmd.run_memory_cli_bridge(
        parser.parse_args(
            [
                "memory",
                "blocks",
                "update",
                "blk-nope",
                "new content",
            ]
        )
    )
    err = capsys.readouterr().err
    assert rc == 1
    payload = json.loads(err)
    assert payload["ok"] is False
    assert payload["code"] == "NOT_FOUND"


def test_update_read_only_is_denied(store, parser, capsys) -> None:
    memory_cmd.run_memory_cli_bridge(
        parser.parse_args(
            [
                "memory",
                "blocks",
                "pin",
                "agent_identity",
                "You are an assistant.",
                "--block-id",
                "blk-id",
                "--agent-id",
                "alpha",
            ]
        )
    )
    capsys.readouterr()
    rc = memory_cmd.run_memory_cli_bridge(
        parser.parse_args(
            [
                "memory",
                "blocks",
                "update",
                "blk-id",
                "should-not-apply",
            ]
        )
    )
    err = capsys.readouterr().err
    assert rc == 2
    payload = json.loads(err)
    assert payload["code"] == "MEMORY_BLOCK_EDIT_DENIED"
    block = store.get_memory_block("blk-id")
    assert block is not None
    assert block.content == "You are an assistant."


def test_unpin_removes_pinned_block(store, parser, capsys) -> None:
    memory_cmd.run_memory_cli_bridge(
        parser.parse_args(
            [
                "memory",
                "blocks",
                "pin",
                "session_pin",
                "Use ripgrep for code search.",
                "--block-id",
                "blk-sess",
                "--agent-id",
                "alpha",
            ]
        )
    )
    capsys.readouterr()
    rc = memory_cmd.run_memory_cli_bridge(
        parser.parse_args(
            [
                "memory",
                "blocks",
                "unpin",
                "blk-sess",
                "--actor",
                "alice",
            ]
        )
    )
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["removed"] is True
    assert store.get_memory_block("blk-sess") is None


def test_unpin_read_only_is_denied(store, parser, capsys) -> None:
    memory_cmd.run_memory_cli_bridge(
        parser.parse_args(
            [
                "memory",
                "blocks",
                "pin",
                "agent_identity",
                "Identity content.",
                "--block-id",
                "blk-id",
                "--agent-id",
                "alpha",
            ]
        )
    )
    capsys.readouterr()
    rc = memory_cmd.run_memory_cli_bridge(
        parser.parse_args(["memory", "blocks", "unpin", "blk-id"])
    )
    err = capsys.readouterr().err
    assert rc == 2
    payload = json.loads(err)
    assert payload["code"] == "MEMORY_BLOCK_EDIT_DENIED"
    assert store.get_memory_block("blk-id") is not None
