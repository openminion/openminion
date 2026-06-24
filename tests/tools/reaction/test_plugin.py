from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from openminion.modules.tool.errors import ToolRuntimeError
from openminion.modules.tool.runtime.policy import Policy
from openminion.modules.tool.registry import ToolRegistry
from openminion.modules.tool.runtime import RuntimeContext

from openminion.tools.reaction.plugin import (
    ReactionsPlugin,
    _h_reactions_list,
    _h_reactions_set,
    clear_channel_adapters,
    emit_signal_reaction_received,
    register_channel_adapter,
)
from openminion.tools.reaction.schemas import ReactionsSetArgs


class _FakeAdapter:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.rows: list[dict] = []

    def react_add(self, message, emoji) -> None:
        self.calls.append(
            ("add", message.channel, message.conversation_id, message.message_id, emoji)
        )

    def react_remove_one(self, message, emoji) -> None:
        self.calls.append(
            (
                "remove_one",
                message.channel,
                message.conversation_id,
                message.message_id,
                emoji,
            )
        )

    def react_remove_all_bot(self, message) -> None:
        self.calls.append(
            (
                "remove_all_bot",
                message.channel,
                message.conversation_id,
                message.message_id,
            )
        )

    def list_reactions(self, message, scope) -> list[dict]:
        self.calls.append(
            (
                "list",
                message.channel,
                message.conversation_id,
                message.message_id,
                scope,
            )
        )
        return list(self.rows)


@pytest.fixture(autouse=True)
def _reset_adapters():
    clear_channel_adapters()
    try:
        yield
    finally:
        clear_channel_adapters()


def _ctx(
    tmp_path: Path, reactions_cfg: dict | None = None, channels_cfg: dict | None = None
) -> RuntimeContext:
    run_root = tmp_path / "run"
    run_root.mkdir(parents=True, exist_ok=True)
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    tools_cfg: dict = {
        "allow_prefix": ["reactions."],
        "deny_exact": [],
        "deny_prefix": [],
    }
    if reactions_cfg is not None:
        tools_cfg["reactions"] = reactions_cfg

    raw_policy: dict = {
        "workspace_root": str(tmp_path / "runs"),
        "tools": tools_cfg,
        "paths": {
            "read_allow": [str(workspace)],
            "write_allow": [str(workspace)],
            "deny": [],
        },
        "commands": {
            "mode": "allowlist",
            "allow": ["echo"],
            "deny_exact": [],
            "deny_regex": [],
        },
    }
    if channels_cfg is not None:
        raw_policy["channels"] = channels_cfg

    policy = Policy(raw=raw_policy)
    return RuntimeContext(
        policy=policy,
        workspace=workspace,
        run_root=run_root,
        scope="WRITE_SAFE",
        confirm=False,
    )


def _message(channel: str = "discord") -> dict:
    return {
        "channel": channel,
        "conversation_id": "conv-1",
        "message_id": "msg-1",
        "account_id": "acct-1",
    }


def _audit_events(ctx: RuntimeContext) -> list[dict]:
    path = Path(ctx.run_root) / "audit.jsonl"
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        rows.append(json.loads(line))
    return rows


def test_plugin_registers_tools():
    registry = ToolRegistry()
    ReactionsPlugin().register(registry)

    names = set(registry.list().keys())
    assert "reactions.set" in names
    assert "reactions.list" in names


def test_set_adds_reaction(tmp_path):
    adapter = _FakeAdapter()
    register_channel_adapter("discord", adapter)
    ctx = _ctx(tmp_path)

    result = _h_reactions_set({"message": _message("discord"), "emoji": "✅"}, ctx)

    assert result["ok"] is True
    assert result["applied"]["action"] == "added"
    assert result["applied"]["emoji"] == "✅"
    assert ("add", "discord", "conv-1", "msg-1", "✅") in adapter.calls


def test_set_empty_emoji_removes_all_bot_reactions(tmp_path):
    adapter = _FakeAdapter()
    register_channel_adapter("slack", adapter)
    ctx = _ctx(tmp_path)

    result = _h_reactions_set({"message": _message("slack"), "emoji": ""}, ctx)

    assert result["ok"] is True
    assert result["applied"]["action"] == "removed_all_bot"
    assert ("remove_all_bot", "slack", "conv-1", "msg-1") in adapter.calls


def test_set_remove_true_removes_specific_emoji(tmp_path):
    adapter = _FakeAdapter()
    register_channel_adapter("telegram", adapter)
    ctx = _ctx(tmp_path)

    result = _h_reactions_set(
        {"message": _message("telegram"), "emoji": "👀", "remove": True}, ctx
    )

    assert result["ok"] is True
    assert result["applied"]["action"] == "removed_one"
    assert ("remove_one", "telegram", "conv-1", "msg-1", "👀") in adapter.calls


def test_set_remove_true_requires_non_empty_emoji():
    with pytest.raises(ValidationError):
        ReactionsSetArgs.model_validate(
            {"message": _message("telegram"), "emoji": "", "remove": True}
        )


def test_set_zalo_requires_non_empty_emoji(tmp_path):
    ctx = _ctx(tmp_path)
    with pytest.raises(ToolRuntimeError) as exc_info:
        _h_reactions_set({"message": _message("zalouser"), "emoji": ""}, ctx)
    assert exc_info.value.code == "INVALID_ARGUMENT"


def test_set_whatsapp_remove_true_maps_to_remove_all(tmp_path):
    adapter = _FakeAdapter()
    register_channel_adapter("whatsapp", adapter)
    ctx = _ctx(tmp_path)

    result = _h_reactions_set(
        {"message": _message("whatsapp"), "emoji": "🙏", "remove": True}, ctx
    )

    assert result["ok"] is True
    assert result["applied"]["action"] == "removed_one"
    assert ("remove_all_bot", "whatsapp", "conv-1", "msg-1") in adapter.calls


def test_set_without_adapter_returns_noop(tmp_path):
    ctx = _ctx(tmp_path)
    result = _h_reactions_set({"message": _message("discord"), "emoji": "✅"}, ctx)
    assert result["ok"] is True
    assert result["applied"]["action"] == "noop"
    assert "adapter_not_configured" in result["warnings"]


def test_set_uses_runtime_message_ref_from_context_when_message_missing(tmp_path):
    adapter = _FakeAdapter()
    register_channel_adapter("discord", adapter)
    ctx = _ctx(tmp_path)
    ctx.message_ref = _message("discord")

    result = _h_reactions_set({"emoji": "✅"}, ctx)

    assert result["ok"] is True
    assert result["applied"]["action"] == "added"
    assert result["message"]["channel"] == "discord"
    assert ("add", "discord", "conv-1", "msg-1", "✅") in adapter.calls


def test_list_uses_runtime_message_ref_from_policy_when_message_missing(tmp_path):
    adapter = _FakeAdapter()
    adapter.rows = [{"emoji": "✅", "count": 1, "reacted_by_bot": True}]
    register_channel_adapter("discord", adapter)
    ctx = _ctx(
        tmp_path,
        reactions_cfg={"runtime_message_ref": _message("discord")},
    )

    result = _h_reactions_list({"scope": "bot_only"}, ctx)

    assert result["ok"] is True
    assert result["reactions"] == [{"emoji": "✅", "count": 1, "reacted_by_bot": True}]
    assert ("list", "discord", "conv-1", "msg-1", "bot_only") in adapter.calls


def test_list_unsupported_channel_returns_not_supported(tmp_path):
    ctx = _ctx(tmp_path)
    result = _h_reactions_list(
        {"message": _message("signal"), "scope": "bot_only"}, ctx
    )

    assert result["ok"] is False
    assert result["reactions"] == []
    assert result["warnings"] == ["not_supported_on_channel"]


def test_list_filters_bot_only_rows(tmp_path):
    adapter = _FakeAdapter()
    adapter.rows = [
        {"emoji": "✅", "count": 4, "reacted_by_bot": True},
        {"emoji": "👀", "count": 2, "reacted_by_bot": False},
    ]
    register_channel_adapter("discord", adapter)
    ctx = _ctx(tmp_path)

    result = _h_reactions_list(
        {"message": _message("discord"), "scope": "bot_only"}, ctx
    )

    assert result["ok"] is True
    assert result["warnings"] == []
    assert result["reactions"] == [{"emoji": "✅", "count": 4, "reacted_by_bot": True}]


def test_policy_gate_denies_reaction_write(tmp_path):
    ctx = _ctx(tmp_path, reactions_cfg={"actions": {"reactions": {"enabled": False}}})
    with pytest.raises(ToolRuntimeError) as exc_info:
        _h_reactions_set({"message": _message("discord"), "emoji": "✅"}, ctx)
    assert exc_info.value.code == "POLICY_DENIED"


def test_channel_policy_gate_denies_reaction_write(tmp_path):
    ctx = _ctx(
        tmp_path,
        reactions_cfg={
            "channels": {
                "discord": {
                    "actions": {
                        "reactions": {
                            "enabled": False,
                        }
                    }
                }
            }
        },
    )
    with pytest.raises(ToolRuntimeError) as exc_info:
        _h_reactions_set({"message": _message("discord"), "emoji": "✅"}, ctx)
    assert exc_info.value.code == "POLICY_DENIED"


def test_set_writes_requested_and_completed_audit_events(tmp_path):
    adapter = _FakeAdapter()
    register_channel_adapter("discord", adapter)
    ctx = _ctx(tmp_path)

    _h_reactions_set({"message": _message("discord"), "emoji": "✅"}, ctx)
    events = _audit_events(ctx)

    assert [row["event"] for row in events] == ["tool.requested", "tool.completed"]
    assert events[0]["tool"] == "reactions.set"
    assert events[1]["applied"]["action"] == "added"


def test_set_failure_writes_failed_audit_event(tmp_path):
    ctx = _ctx(tmp_path, reactions_cfg={"actions": {"reactions": {"enabled": False}}})
    with pytest.raises(ToolRuntimeError):
        _h_reactions_set({"message": _message("discord"), "emoji": "✅"}, ctx)

    events = _audit_events(ctx)
    assert [row["event"] for row in events] == ["tool.requested", "tool.failed"]
    assert events[-1]["error"]["code"] == "POLICY_DENIED"


def test_emit_signal_reaction_received_when_enabled(tmp_path):
    ctx = _ctx(
        tmp_path,
        reactions_cfg={"channels": {"signal": {"reactionNotifications": True}}},
    )
    emitted = emit_signal_reaction_received(ctx, {"emoji": "✅", "actor": "user-1"})
    assert emitted is True

    events = _audit_events(ctx)
    assert len(events) == 1
    assert events[0]["event"] == "signal.reaction_received"


def test_emit_signal_reaction_received_is_noop_when_disabled(tmp_path):
    ctx = _ctx(tmp_path)
    emitted = emit_signal_reaction_received(ctx, {"emoji": "✅"})
    assert emitted is False
    assert _audit_events(ctx) == []


def test_emit_event_failing_audit_sink_logs_structured_warning(
    tmp_path, caplog, monkeypatch
):
    from openminion.tools.reaction import plugin as reactions_plugin

    adapter = _FakeAdapter()
    register_channel_adapter("discord", adapter)
    ctx = _ctx(tmp_path)

    def _boom(_event):
        raise RuntimeError("audit sink unavailable")

    monkeypatch.setattr(ctx, "write_audit_event", _boom)

    with caplog.at_level("WARNING", logger=reactions_plugin.__name__):
        result = _h_reactions_set({"message": _message("discord"), "emoji": "✅"}, ctx)

    assert result["ok"] is True
    warning_records = [
        r
        for r in caplog.records
        if r.levelname == "WARNING"
        and "reactions audit event emission failed" in r.getMessage()
    ]
    assert warning_records, "expected structured warning when audit sink fails"
    assert any("RuntimeError" in r.getMessage() for r in warning_records)
