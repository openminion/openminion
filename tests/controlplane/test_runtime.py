from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from openminion.modules.controlplane.runtime.audit import AuditLogger
from openminion.modules.controlplane.runtime.auth import AuthEvaluator
from openminion.modules.controlplane.runtime.parser import SlashCommandParser
from openminion.modules.controlplane.commands.registry import CommandRegistry
from openminion.modules.controlplane.config import ControlPlaneConfig, load_config
from openminion.modules.controlplane.contracts.models import (
    InboundMessage,
    ResolvedContext,
)
from openminion.modules.controlplane.runtime.router import Router
from openminion.modules.controlplane.runtime import EchoBrain, RuntimeCoordinator
from openminion.modules.controlplane.storage.sqlite import SQLiteControlPlaneStore
from openminion.modules.controlplane.runtime.store import InMemoryControlPlaneStore


def _make_runtime(
    store: InMemoryControlPlaneStore | None = None,
    admin_keys: list[str] | None = None,
    brain_client: EchoBrain | None = None,
) -> tuple[RuntimeCoordinator, list[dict], AuditLogger]:
    store = store or InMemoryControlPlaneStore()
    auth = AuthEvaluator(admin_user_keys=admin_keys or [])
    router = Router(store)
    parser = SlashCommandParser()
    registry = CommandRegistry(store=store, auth=auth)
    brain = brain_client or EchoBrain()
    outbound: list[dict] = []
    audit = AuditLogger()
    rt = RuntimeCoordinator(
        store=store,
        router=router,
        parser=parser,
        command_registry=registry,
        brain_client=brain,
        outbound=outbound.append,
        audit_logger=audit,
    )
    return rt, outbound, audit


def _ctx(
    user_key: str = "u1", session_id: str = "s1", agent_id: str = "agent:default"
) -> ResolvedContext:
    return ResolvedContext(
        user_key=user_key,
        chat_key="chat1",
        session_id=session_id,
        agent_id=agent_id,
        role="user",
        trace_id="trace-x",
        span_id="span-x",
    )


def test_load_config_defaults() -> None:
    cfg = load_config(None)
    assert cfg.default_agent_id == "agent:default"
    assert cfg.command_prefix == "/"
    assert cfg.store_backend == "sqlite"


def test_load_config_from_dict() -> None:
    cfg = load_config(
        {"default_agent_id": "agent:custom", "admin_user_keys": ["user:admin"]}
    )
    assert cfg.default_agent_id == "agent:custom"
    assert "user:admin" in cfg.admin_user_keys


def test_load_config_from_missing_path(tmp_path: Path) -> None:
    cfg = load_config(tmp_path / "nonexistent.yaml")
    assert cfg.default_agent_id == "agent:default"  # falls back to defaults


def test_auth_evaluator_role_user() -> None:
    auth = AuthEvaluator(admin_user_keys=["user:admin"])
    assert auth.role_for("user:bob") == "user"
    assert auth.is_admin("user:bob") is False


def test_auth_evaluator_role_admin() -> None:
    auth = AuthEvaluator(admin_user_keys=["user:admin"])
    assert auth.role_for("user:admin") == "admin"
    assert auth.is_admin("user:admin") is True


def test_auth_check_allows_normal_command() -> None:
    auth = AuthEvaluator(admin_user_keys=[])
    allowed, _ = auth.check("user:anyone", "session.new")
    assert allowed is True


def test_auth_check_denies_admin_command_for_user() -> None:
    auth = AuthEvaluator(admin_user_keys=["user:admin"])
    allowed, reason = auth.check("user:bob", "artifact.purge")
    assert allowed is False
    assert "admin" in reason


def test_auth_check_allows_admin_command_for_admin() -> None:
    auth = AuthEvaluator(admin_user_keys=["user:admin"])
    allowed, _ = auth.check("user:admin", "artifact.purge")
    assert allowed is True


def test_parser_slash_command() -> None:
    parser = SlashCommandParser()
    cmd = parser.parse("/help")
    assert cmd is not None
    assert cmd.canonical == "help"


def test_parser_space_form() -> None:
    parser = SlashCommandParser()
    cmd = parser.parse("/agent ls")
    assert cmd is not None
    assert cmd.canonical == "agent.ls"


def test_parser_dot_form() -> None:
    parser = SlashCommandParser()
    cmd = parser.parse("/agent.use brain")
    assert cmd is not None
    assert cmd.canonical == "agent.use"
    assert "brain" in cmd.args


def test_parser_non_command_returns_none() -> None:
    parser = SlashCommandParser()
    assert parser.parse("just chat") is None
    assert parser.parse("") is None


def test_parser_bare_slash_returns_none() -> None:
    parser = SlashCommandParser()
    assert parser.parse("/") is None


def test_command_help_lists_all_commands() -> None:
    store = InMemoryControlPlaneStore()
    registry = CommandRegistry(store=store)
    parser = SlashCommandParser()
    cmd = parser.parse("/help")
    assert cmd is not None
    result = registry.execute(cmd, _ctx())
    assert result.ok
    assert "session.new" in result.text


def test_command_agent_ls() -> None:
    store = InMemoryControlPlaneStore()
    registry = CommandRegistry(store=store)
    parser = SlashCommandParser()
    cmd = parser.parse("/agent ls")
    assert cmd is not None
    result = registry.execute(cmd, _ctx())
    assert result.ok
    assert "agent:default" in result.text


def test_command_agent_use() -> None:
    store = InMemoryControlPlaneStore()
    registry = CommandRegistry(store=store)
    parser = SlashCommandParser()
    cmd = parser.parse("/agent use agent:brain")
    assert cmd is not None
    result = registry.execute(cmd, _ctx(session_id="sess-0001"))
    assert result.ok
    assert "agent:brain" in result.text


def test_command_session_id() -> None:
    store = InMemoryControlPlaneStore()
    registry = CommandRegistry(store=store)
    parser = SlashCommandParser()
    cmd = parser.parse("/session id")
    assert cmd is not None
    result = registry.execute(cmd, _ctx(session_id="sess-xyz"))
    assert result.ok
    assert "sess-xyz" in result.text


def test_command_session_status() -> None:
    store = InMemoryControlPlaneStore()
    _ = store.resolve_session("u1", "c1")
    store.append_turn(session_id="sess-0001", role="user", content="hi")
    registry = CommandRegistry(store=store)
    parser = SlashCommandParser()
    cmd = parser.parse("/session status")
    assert cmd is not None
    result = registry.execute(cmd, _ctx(session_id="sess-0001"))
    assert result.ok
    assert "sess-0001" in result.text


def test_command_unknown_returns_error() -> None:
    store = InMemoryControlPlaneStore()
    registry = CommandRegistry(store=store)
    parser = SlashCommandParser()
    cmd = parser.parse("/nonexistent command")
    assert cmd is not None
    result = registry.execute(cmd, _ctx())
    assert not result.ok
    assert "Unknown" in result.text


def test_auth_denial_reflected_in_command_result() -> None:
    store = InMemoryControlPlaneStore()
    auth = AuthEvaluator(admin_user_keys=["user:admin"])
    registry = CommandRegistry(store=store, auth=auth)
    parser = SlashCommandParser()
    cmd = parser.parse("/artifact purge")
    assert cmd is not None
    ctx = _ctx(user_key="user:bob")
    result = registry.execute(cmd, ctx)
    assert not result.ok
    assert "denied" in result.text.lower() or "permission" in result.text.lower()


def test_agent_use_missing_arg_returns_error() -> None:
    store = InMemoryControlPlaneStore()
    registry = CommandRegistry(store=store)
    parser = SlashCommandParser()
    cmd = parser.parse("/agent use")
    assert cmd is not None
    cmd_no_arg = type(cmd)(canonical="agent.use", original_text="/agent use", args=[])
    result = registry.execute(cmd_no_arg, _ctx())
    assert not result.ok


def test_memory_promote_missing_arg_returns_error() -> None:
    store = InMemoryControlPlaneStore()
    registry = CommandRegistry(store=store)
    type(_ctx()).__class__  # just to not import manually; use parser instead
    from openminion.modules.controlplane.contracts.models import ParsedCommand

    cmd_obj = ParsedCommand(
        canonical="memory.promote", original_text="/memory promote", args=[]
    )
    result = registry.execute(cmd_obj, _ctx(user_key="user:admin"))
    assert not result.ok


def test_config_set_missing_args_returns_error() -> None:
    from openminion.modules.controlplane.contracts.models import ParsedCommand

    store = InMemoryControlPlaneStore()
    auth = AuthEvaluator(admin_user_keys=["user:admin"])
    registry = CommandRegistry(store=store, auth=auth)
    cmd_obj = ParsedCommand(
        canonical="config.set", original_text="/config set", args=["only_key"]
    )
    result = registry.execute(cmd_obj, _ctx(user_key="user:admin"))
    assert not result.ok


# Runtime end-to-end tests — CP-023


def test_runtime_chat_path_invokes_brain() -> None:
    rt, outbound, audit = _make_runtime()
    inbound = InboundMessage(user_key="u1", chat_key="c1", text="hello")
    payload = rt.handle_inbound(inbound)
    assert payload["type"] == "chat"
    assert "hello" in payload["text"]
    assert outbound[-1]["type"] == "chat"


def test_runtime_is_dispatcher_backed_shim() -> None:
    rt, outbound, _ = _make_runtime()
    called: dict[str, str] = {}
    from openminion.modules.controlplane.contracts.outbound import from_legacy_payload

    def _fake_dispatch(inbound: InboundMessage) -> tuple[dict, ResolvedContext]:
        called["text"] = inbound.text
        payload = from_legacy_payload(
            {
                "type": "chat",
                "text": "shim-payload",
                "session_id": "sess-shim",
                "agent_id": "agent:shim",
            },
            ctx=_ctx(session_id="sess-shim", agent_id="agent:shim"),
        )
        return payload, _ctx(session_id="sess-shim", agent_id="agent:shim")

    rt.dispatcher.dispatch = _fake_dispatch  # type: ignore[method-assign]
    payload = rt.handle_inbound(
        InboundMessage(user_key="u1", chat_key="c1", text="hello shim")
    )
    assert called["text"] == "hello shim"
    assert payload["text"] == "shim-payload"
    assert outbound[-1]["text"] == "shim-payload"


class _ClarifyBrain:
    contract_version = "v1"

    def __init__(self) -> None:
        self.calls = 0
        self.trace_ids: list[str] = []

    def run(
        self,
        *,
        session_id: str,
        agent_id: str,
        user_text: str | None,
        attachment_refs: list[str],
        trace_id: str,
    ) -> dict[str, Any]:
        self.calls += 1
        self.trace_ids.append(trace_id)
        if self.calls == 1:
            return {
                "text": "Which location should I check weather for?",
                "status": "waiting_user",
                "trace_id": trace_id,
                "clarify_request": {
                    "clarify_id": "clarify-weather-1",
                    "trace_id": trace_id,
                    "session_id": session_id,
                    "blocking": True,
                    "questions": [
                        {
                            "id": "q-location",
                            "type": "missing_field",
                            "question": "Which location should I check weather for?",
                            "is_blocking": True,
                        }
                    ],
                },
            }
        return {
            "text": "Weather for San Diego: 66F and sunny.",
            "status": "completed",
            "trace_id": trace_id,
        }


def test_runtime_clarify_waiting_user_and_same_trace_resume() -> None:
    brain = _ClarifyBrain()
    rt, outbound, audit = _make_runtime(brain_client=brain)

    first = rt.handle_inbound(
        InboundMessage(user_key="u1", chat_key="c1", text="what's weather today?")
    )
    assert first["type"] == "chat"
    assert first["status"] == "waiting_user"
    assert first["clarify"] is not None
    assert first["clarify"]["clarify_id"] == "clarify-weather-1"

    resumed = rt.handle_inbound(
        InboundMessage(
            user_key="u1",
            chat_key="c1",
            text="San Diego",
            metadata={
                "clarify_answer": {
                    "clarify_id": "clarify-weather-1",
                    "question_id": "q-location",
                    "answer": "San Diego",
                }
            },
        )
    )
    assert resumed["status"] == "completed"
    assert brain.calls == 2
    assert brain.trace_ids[0] == brain.trace_ids[1]

    event_types = [
        e.event_type if hasattr(e, "event_type") else str(e.get("event", ""))
        for e in audit.events
    ]
    assert "cp.clarify.requested" in event_types
    assert "cp.clarify.answered" in event_types
    assert "cp.resume.dispatched" in event_types
    assert outbound[-1]["status"] == "completed"


def test_runtime_clarify_resume_accepts_metadata_field() -> None:
    brain = _ClarifyBrain()
    rt, outbound, _ = _make_runtime(brain_client=brain)

    _ = rt.handle_inbound(
        InboundMessage(user_key="u1", chat_key="c1", text="what's weather today?")
    )
    resumed = rt.handle_inbound(
        InboundMessage(
            user_key="u1",
            chat_key="c1",
            text="San Diego",
            metadata={
                "clarify_answer": {
                    "clarify_id": "clarify-weather-1",
                    "question_id": "q-location",
                    "answer": "San Diego",
                }
            },
        )
    )

    assert resumed["status"] == "completed"
    assert brain.calls == 2
    assert outbound[-1]["status"] == "completed"


def test_runtime_rejects_unknown_clarify_id_without_brain_dispatch() -> None:
    brain = _ClarifyBrain()
    rt, outbound, audit = _make_runtime(brain_client=brain)

    _ = rt.handle_inbound(
        InboundMessage(user_key="u1", chat_key="c1", text="what's weather today?")
    )
    bad = rt.handle_inbound(
        InboundMessage(
            user_key="u1",
            chat_key="c1",
            text="San Diego",
            metadata={
                "clarify_answer": {
                    "clarify_id": "clarify-wrong",
                    "question_id": "q-location",
                    "answer": "San Diego",
                }
            },
        )
    )

    assert bad["type"] == "clarify_error"
    assert bad["status"] == "waiting_user"
    assert bad["data"]["error_code"] == "UNKNOWN_CLARIFY_ID"
    assert brain.calls == 1
    event_types = [
        e.event_type if hasattr(e, "event_type") else str(e.get("event", ""))
        for e in audit.events
    ]
    assert "cp.clarify.answer_rejected" in event_types
    assert outbound[-1]["type"] == "clarify_error"


def test_runtime_clarify_resume_uses_session_scoped_pending_lookup() -> None:
    brain = _ClarifyBrain()
    rt, _, _ = _make_runtime(brain_client=brain)

    first = rt.handle_inbound(
        InboundMessage(user_key="u1", chat_key="c1", text="what's weather today?")
    )
    session_id = str(first["session_id"])
    rt.store.bind_session("u1", "c2", session_id)

    resumed = rt.handle_inbound(
        InboundMessage(
            user_key="u1",
            chat_key="c2",
            text="San Diego",
            metadata={
                "clarify_answer": {
                    "clarify_id": "clarify-weather-1",
                    "question_id": "q-location",
                    "answer": "San Diego",
                }
            },
        )
    )

    assert resumed["status"] == "completed"
    assert brain.calls == 2
    assert brain.trace_ids[0] == brain.trace_ids[1]


def test_runtime_command_help() -> None:
    rt, outbound, audit = _make_runtime()
    inbound = InboundMessage(user_key="u1", chat_key="c1", text="/help")
    payload = rt.handle_inbound(inbound)
    assert payload["ok"] is True
    assert outbound[-1]["type"] == "command_result"


def test_runtime_session_routing_is_stable() -> None:
    rt, _, _ = _make_runtime()
    msg1 = InboundMessage(user_key="u1", chat_key="c1", text="first")
    msg2 = InboundMessage(user_key="u1", chat_key="c1", text="second")
    r1 = rt.handle_inbound(msg1)
    r2 = rt.handle_inbound(msg2)
    assert r1["session_id"] == r2["session_id"]


def test_runtime_different_chat_keys_get_different_sessions() -> None:
    rt, _, _ = _make_runtime()
    m1 = InboundMessage(user_key="u1", chat_key="c1", text="hi")
    m2 = InboundMessage(user_key="u1", chat_key="c2", text="hi")
    r1 = rt.handle_inbound(m1)
    r2 = rt.handle_inbound(m2)
    assert r1["session_id"] != r2["session_id"]


def test_runtime_audit_events_recorded() -> None:
    rt, _, audit = _make_runtime()
    inbound = InboundMessage(user_key="u1", chat_key="c1", text="hello")
    rt.handle_inbound(inbound)
    types = [
        e.event_type if hasattr(e, "event_type") else e.get("event", "")
        for e in audit.events
    ]
    joined = " ".join(str(t) for t in types)
    assert "inbound" in joined or "received" in joined


def test_runtime_command_audit_events_recorded() -> None:
    rt, _, audit = _make_runtime()
    rt.handle_inbound(InboundMessage(user_key="u1", chat_key="c1", text="/session new"))
    types_str = " ".join(
        str(e.get("event", "") if isinstance(e, dict) else getattr(e, "event_type", ""))
        for e in audit.events
    )
    assert "command" in types_str


def test_runtime_session_new_command_changes_session() -> None:
    rt, _, _ = _make_runtime()
    msg = InboundMessage(user_key="u1", chat_key="c1", text="hi")
    r1 = rt.handle_inbound(msg)
    rt.handle_inbound(InboundMessage(user_key="u1", chat_key="c1", text="/session new"))
    msg2 = InboundMessage(user_key="u1", chat_key="c1", text="hi again")
    r2 = rt.handle_inbound(msg2)
    assert r1["session_id"] != r2["session_id"]


# SQLite store tests — CP-007/008


def test_sqlite_store_schema_migration(tmp_path: Path) -> None:
    store = SQLiteControlPlaneStore(tmp_path / "cp.db")
    # migration table should exist
    row = store._conn.execute("SELECT count(*) as n FROM cp_migrations").fetchone()
    assert row["n"] >= 1
    store.close()


def test_sqlite_store_chat_bindings(tmp_path: Path) -> None:
    store = SQLiteControlPlaneStore(tmp_path / "cp.db")
    store.set_chat_binding("c1", "sess-001", "agent:default")
    rec = store.get_chat_binding("c1")
    assert rec is not None
    assert rec["session_id"] == "sess-001"
    store.close()


def test_sqlite_store_user_upsert(tmp_path: Path) -> None:
    store = SQLiteControlPlaneStore(tmp_path / "cp.db")
    store.upsert_user("user:alice", role="admin", profile_meta={"name": "Alice"})
    user = store.get_user("user:alice")
    assert user is not None
    assert user["role"] == "admin"
    store.close()


def test_sqlite_store_put_inbound(tmp_path: Path) -> None:
    store = SQLiteControlPlaneStore(tmp_path / "cp.db")
    row_id = store.put_inbound(
        chat_key="c1",
        user_key="u1",
        text="hello",
        payload={"text": "hello"},
        session_id="sess-001",
    )
    assert row_id > 0
    store.close()


def test_sqlite_store_put_audit_and_list(tmp_path: Path) -> None:
    store = SQLiteControlPlaneStore(tmp_path / "cp.db")
    from openminion.modules.controlplane.runtime.audit import AuditEvent

    ev = AuditEvent(event_type="controlplane.test", session_id="sess-001")
    store.put_audit(ev)
    events = store.list_audit(event_type="controlplane.test")
    assert len(events) == 1
    assert events[0]["session_id"] == "sess-001"
    store.close()


def test_audit_logger_emit_and_filter() -> None:
    logger = AuditLogger()
    logger.emit("controlplane.inbound.received", session_id="s1", trace_id="t1")
    logger.emit("controlplane.command.detected", session_id="s1", trace_id="t2")
    logger.emit("controlplane.outbound.sent", session_id="s2")
    assert len(logger.list_events(session_id="s1")) == 2
    assert len(logger.list_events(event_type="controlplane.outbound.sent")) == 1
    assert len(logger.list_events(trace_id="t1")) == 1


def test_audit_logger_sink_persists_to_sqlite(tmp_path: Path) -> None:
    store = SQLiteControlPlaneStore(tmp_path / "cp.db")
    logger = AuditLogger(sink=store.put_audit)
    logger.emit("controlplane.inbound.received", session_id="sess-x")
    events = store.list_audit(event_type="controlplane.inbound.received")
    assert len(events) == 1
    store.close()


def test_inmemory_store_pending_clarify_roundtrip() -> None:
    store = InMemoryControlPlaneStore()
    payload = {
        "clarify_id": "clarify-1",
        "trace_id": "trace-1",
        "questions": [{"id": "q1", "question": "Which city?"}],
    }
    store.set_pending_clarify("sess-123", payload)
    loaded = store.get_pending_clarify("sess-123")
    assert loaded is not None
    assert loaded["clarify_id"] == "clarify-1"
    store.clear_pending_clarify("sess-123")
    assert store.get_pending_clarify("sess-123") is None


def test_sqlite_store_pending_clarify_roundtrip_restart_safe(tmp_path: Path) -> None:
    db_path = tmp_path / "cp.db"
    store = SQLiteControlPlaneStore(db_path)
    payload = {
        "clarify_id": "clarify-2",
        "trace_id": "trace-2",
        "questions": [{"id": "q2", "question": "Choose one"}],
    }
    store.set_pending_clarify("sess-abc", payload)
    store.close()

    reopened = SQLiteControlPlaneStore(db_path)
    loaded = reopened.get_pending_clarify("sess-abc")
    assert loaded is not None
    assert loaded["clarify_id"] == "clarify-2"
    reopened.clear_pending_clarify("sess-abc")
    assert reopened.get_pending_clarify("sess-abc") is None
    reopened.close()


# Config — extended


def test_load_config_from_json_file(tmp_path: Path) -> None:
    cfg_file = tmp_path / "cp.json"
    cfg_file.write_text(
        '{"default_agent_id": "agent:gpt", "idle_minutes": 30}', encoding="utf-8"
    )
    cfg = load_config(cfg_file)
    assert cfg.default_agent_id == "agent:gpt"
    assert cfg.idle_minutes == 30


def test_load_config_from_config_instance() -> None:
    original = ControlPlaneConfig(default_agent_id="agent:x")
    result = load_config(original)
    assert result is original  # pass-through


def test_load_config_wal_default() -> None:
    cfg = load_config(None)
    assert cfg.wal is True


def test_load_config_admin_user_keys_preserved() -> None:
    cfg = load_config({"admin_user_keys": ["user:root", "user:ops"]})
    assert "user:root" in cfg.admin_user_keys
    assert "user:ops" in cfg.admin_user_keys


def test_load_config_empty_admin_keys_by_default() -> None:
    cfg = load_config(None)
    assert cfg.admin_user_keys == []


# Auth — extended edge cases


def test_auth_empty_admin_list_no_admins() -> None:
    auth = AuthEvaluator(admin_user_keys=[])
    assert auth.is_admin("user:anyone") is False


def test_auth_multiple_admins() -> None:
    auth = AuthEvaluator(admin_user_keys=["user:a", "user:b"])
    assert auth.is_admin("user:a") is True
    assert auth.is_admin("user:b") is True
    assert auth.is_admin("user:c") is False


def test_auth_check_memory_promote_denied_for_user() -> None:
    auth = AuthEvaluator(admin_user_keys=["user:admin"])
    allowed, reason = auth.check("user:bob", "memory.promote")
    assert allowed is False
    assert "admin" in reason


def test_auth_check_config_set_denied_for_user() -> None:
    auth = AuthEvaluator(admin_user_keys=["user:admin"])
    allowed, _ = auth.check("user:bob", "config.set")
    assert allowed is False


def test_auth_check_config_set_allowed_for_admin() -> None:
    auth = AuthEvaluator(admin_user_keys=["user:admin"])
    allowed, _ = auth.check("user:admin", "config.set")
    assert allowed is True


# Parser — extended edge cases


def test_parser_preserves_original_text() -> None:
    parser = SlashCommandParser()
    cmd = parser.parse("/agent use agent:brain")
    assert cmd is not None
    assert cmd.original_text == "/agent use agent:brain"


def test_parser_multi_arg_command() -> None:
    parser = SlashCommandParser()
    cmd = parser.parse("/config set key value")
    assert cmd is not None
    assert cmd.canonical == "config.set"
    assert "key" in cmd.args
    assert "value" in cmd.args


def test_parser_uppercase_command_normalised() -> None:
    parser = SlashCommandParser()
    cmd = parser.parse("/HELP")
    assert cmd is not None
    assert cmd.canonical == "help"


def test_parser_leading_trailing_spaces() -> None:
    parser = SlashCommandParser()
    cmd = parser.parse("  /help  ")
    assert cmd is not None
    assert cmd.canonical == "help"


def test_parser_session_new_space_form() -> None:
    parser = SlashCommandParser()
    cmd = parser.parse("/session new")
    assert cmd is not None
    assert cmd.canonical == "session.new"


def test_parser_dotted_with_extra_args() -> None:
    parser = SlashCommandParser()
    cmd = parser.parse("/memory.promote mem-abc")
    assert cmd is not None
    assert cmd.canonical == "memory.promote"
    assert "mem-abc" in cmd.args


# Command registry — remaining handlers


def test_command_agent_info_known_agent() -> None:
    from openminion.modules.controlplane.contracts.models import ParsedCommand

    store = InMemoryControlPlaneStore()
    registry = CommandRegistry(store=store)
    cmd = ParsedCommand(
        canonical="agent.info",
        original_text="/agent info agent:default",
        args=["agent:default"],
    )
    result = registry.execute(cmd, _ctx())
    assert result.ok
    assert "agent:default" in result.text


def test_command_agent_info_unknown_agent() -> None:
    from openminion.modules.controlplane.contracts.models import ParsedCommand

    store = InMemoryControlPlaneStore()
    registry = CommandRegistry(store=store)
    cmd = ParsedCommand(
        canonical="agent.info",
        original_text="/agent info agent:ghost",
        args=["agent:ghost"],
    )
    result = registry.execute(cmd, _ctx())
    assert not result.ok
    assert "not found" in result.text.lower()


def test_command_agent_stop() -> None:
    from openminion.modules.controlplane.contracts.models import ParsedCommand

    store = InMemoryControlPlaneStore()
    registry = CommandRegistry(store=store)
    cmd = ParsedCommand(canonical="agent.stop", original_text="/agent stop", args=[])
    result = registry.execute(cmd, _ctx(session_id="sess-1", agent_id="agent:default"))
    assert result.ok
    assert "stopped" in result.text


def test_command_job_ls() -> None:
    from openminion.modules.controlplane.contracts.models import ParsedCommand

    store = InMemoryControlPlaneStore()
    registry = CommandRegistry(store=store)
    cmd = ParsedCommand(canonical="job.ls", original_text="/job ls", args=[])
    result = registry.execute(cmd, _ctx())
    assert result.ok
    assert result.data["jobs"] == []


def test_command_artifact_ls() -> None:
    from openminion.modules.controlplane.contracts.models import ParsedCommand

    store = InMemoryControlPlaneStore()
    registry = CommandRegistry(store=store)
    cmd = ParsedCommand(canonical="artifact.ls", original_text="/artifact ls", args=[])
    result = registry.execute(cmd, _ctx())
    assert result.ok


def test_command_memory_ls() -> None:
    from openminion.modules.controlplane.contracts.models import ParsedCommand

    store = InMemoryControlPlaneStore()
    registry = CommandRegistry(store=store)
    cmd = ParsedCommand(canonical="memory.ls", original_text="/memory ls", args=[])
    result = registry.execute(cmd, _ctx())
    assert result.ok
    assert result.data["candidates"] == []


def test_command_config_show() -> None:
    from openminion.modules.controlplane.contracts.models import ParsedCommand

    store = InMemoryControlPlaneStore()
    registry = CommandRegistry(store=store)
    cmd = ParsedCommand(canonical="config.show", original_text="/config show", args=[])
    result = registry.execute(cmd, _ctx())
    assert result.ok


def test_command_config_set_allowed_for_admin() -> None:
    from openminion.modules.controlplane.contracts.models import ParsedCommand

    store = InMemoryControlPlaneStore()
    auth = AuthEvaluator(admin_user_keys=["user:admin"])
    registry = CommandRegistry(store=store, auth=auth)
    cmd = ParsedCommand(
        canonical="config.set", original_text="/config set k v", args=["k", "v"]
    )
    result = registry.execute(cmd, _ctx(user_key="user:admin"))
    assert result.ok
    assert result.data["key"] == "k"
    assert result.data["value"] == "v"


def test_command_memory_promote_with_arg() -> None:
    from openminion.modules.controlplane.contracts.models import ParsedCommand

    store = InMemoryControlPlaneStore()
    auth = AuthEvaluator(admin_user_keys=["user:admin"])
    registry = CommandRegistry(store=store, auth=auth)
    cmd = ParsedCommand(
        canonical="memory.promote",
        original_text="/memory promote mem-1",
        args=["mem-1"],
    )
    result = registry.execute(cmd, _ctx(user_key="user:admin"))
    assert result.ok
    assert "mem-1" in result.text


def test_command_artifact_purge_allowed_for_admin() -> None:
    from openminion.modules.controlplane.contracts.models import ParsedCommand

    store = InMemoryControlPlaneStore()
    auth = AuthEvaluator(admin_user_keys=["user:admin"])
    registry = CommandRegistry(store=store, auth=auth)
    cmd = ParsedCommand(
        canonical="artifact.purge", original_text="/artifact purge", args=[]
    )
    result = registry.execute(cmd, _ctx(user_key="user:admin"))
    assert result.ok


def test_help_hides_admin_commands_from_user() -> None:
    from openminion.modules.controlplane.contracts.models import ParsedCommand

    store = InMemoryControlPlaneStore()
    auth = AuthEvaluator(admin_user_keys=["user:admin"])
    registry = CommandRegistry(store=store, auth=auth)
    cmd = ParsedCommand(canonical="help", original_text="/help", args=[])
    result = registry.execute(cmd, _ctx(user_key="user:regular"))
    assert result.ok
    assert "artifact.purge" not in result.text
    assert "memory.promote" not in result.text


def test_help_shows_admin_commands_to_admin() -> None:
    from openminion.modules.controlplane.contracts.models import ParsedCommand

    store = InMemoryControlPlaneStore()
    auth = AuthEvaluator(admin_user_keys=["user:admin"])
    registry = CommandRegistry(store=store, auth=auth)
    cmd = ParsedCommand(canonical="help", original_text="/help", args=[])
    result = registry.execute(cmd, _ctx(user_key="user:admin"))
    assert result.ok
    assert "artifact.purge" in result.text


# InMemoryControlPlaneStore — extended


def test_store_session_agent_switch_persists() -> None:
    store = InMemoryControlPlaneStore()
    session_id = store.resolve_session("u1", "c1")
    store.set_agent(session_id, "agent:brain")
    assert store.resolve_agent(session_id) == "agent:brain"


def test_store_unknown_agent_raises() -> None:
    store = InMemoryControlPlaneStore()
    session_id = store.resolve_session("u1", "c1")
    with pytest.raises(ValueError, match="unknown agent"):
        store.set_agent(session_id, "agent:does_not_exist")


def test_store_ensure_agent_registers_new_agent() -> None:
    store = InMemoryControlPlaneStore()
    store.ensure_agent("agent:custom", name="Custom")
    agents = {a["id"]: a for a in store.list_agents()}
    assert "agent:custom" in agents
    assert agents["agent:custom"]["name"] == "Custom"


def test_store_new_session_creates_different_id() -> None:
    store = InMemoryControlPlaneStore()
    s1 = store.resolve_session("u1", "c1")
    s2 = store.new_session("u1", "c1")
    assert s1 != s2


def test_store_append_turn_and_list_turns() -> None:
    store = InMemoryControlPlaneStore()
    session_id = store.resolve_session("u1", "c1")
    store.append_turn(session_id=session_id, role="user", content="hey", meta={"x": 1})
    store.append_turn(session_id=session_id, role="assistant", content="hello back")
    turns = store.list_turns(session_id)
    assert len(turns) == 2
    assert turns[0].role == "user"
    assert turns[1].role == "assistant"


def test_store_attachment_refs_from_inputs() -> None:
    from openminion.modules.controlplane.contracts.models import AttachmentInput

    store = InMemoryControlPlaneStore()
    inputs = [
        AttachmentInput(name="file.txt", mime="text/plain", data=b"hello"),
        AttachmentInput(
            name="img.png", mime="image/png", url="http://example.com/img.png"
        ),
    ]
    refs = store.attachment_refs_from_inputs(inputs)
    assert len(refs) == 2
    # URL-based ref uses the provided URL
    assert refs[1] == "http://example.com/img.png"
    # Data-based ref is an artifact:// URI
    assert refs[0].startswith("artifact://")


# Router — extended


def test_router_different_users_same_chat_get_different_sessions() -> None:
    store = InMemoryControlPlaneStore()
    router = Router(store)
    m1 = InboundMessage(user_key="u1", chat_key="c1", text="hi")
    m2 = InboundMessage(user_key="u2", chat_key="c1", text="hi")
    ctx1 = router.resolve(m1)
    ctx2 = router.resolve(m2)
    # Different users always get independent sessions
    assert ctx1.session_id != ctx2.session_id


def test_router_trace_id_is_unique_per_call() -> None:
    store = InMemoryControlPlaneStore()
    router = Router(store)
    msg = InboundMessage(user_key="u1", chat_key="c1", text="hello")
    ctx1 = router.resolve(msg)
    ctx2 = router.resolve(msg)
    assert ctx1.trace_id != ctx2.trace_id


def test_router_uses_inbound_trace_id_when_provided() -> None:
    store = InMemoryControlPlaneStore()
    router = Router(store)
    msg = InboundMessage(
        user_key="u1",
        chat_key="c1",
        text="resume",
        metadata={"trace_id": "trace-resume-123"},
    )
    ctx = router.resolve(msg)
    assert ctx.trace_id == "trace-resume-123"


def test_router_resolved_context_fields() -> None:
    store = InMemoryControlPlaneStore()
    router = Router(store)
    msg = InboundMessage(user_key="u1", chat_key="c1", text="hello", channel="telegram")
    ctx = router.resolve(msg)
    assert ctx.user_key == "u1"
    assert ctx.chat_key == "c1"
    assert ctx.session_id != ""
    assert ctx.agent_id == "agent:default"
    assert ctx.trace_id != ""


# SQLite store — extended


def test_sqlite_store_chat_binding_overwrite(tmp_path: Path) -> None:
    store = SQLiteControlPlaneStore(tmp_path / "cp.db")
    store.set_chat_binding("c1", "sess-001")
    store.set_chat_binding("c1", "sess-002")  # overwrite
    rec = store.get_chat_binding("c1")
    assert rec is not None
    assert rec["session_id"] == "sess-002"
    store.close()


def test_sqlite_store_missing_chat_binding_returns_none(tmp_path: Path) -> None:
    store = SQLiteControlPlaneStore(tmp_path / "cp.db")
    rec = store.get_chat_binding("nonexistent")
    assert rec is None
    store.close()


def test_sqlite_store_user_default_role(tmp_path: Path) -> None:
    store = SQLiteControlPlaneStore(tmp_path / "cp.db")
    store.upsert_user("user:bob")
    user = store.get_user("user:bob")
    assert user is not None
    assert user["role"] == "user"
    store.close()


def test_sqlite_store_missing_user_returns_none(tmp_path: Path) -> None:
    store = SQLiteControlPlaneStore(tmp_path / "cp.db")
    assert store.get_user("user:nobody") is None
    store.close()


def test_sqlite_store_put_outbound(tmp_path: Path) -> None:
    store = SQLiteControlPlaneStore(tmp_path / "cp.db")
    row_id = store.put_outbound(
        chat_key="c1",
        text="hello back",
        payload={"text": "hello back", "type": "chat"},
        session_id="sess-001",
    )
    assert row_id > 0
    store.close()


def test_sqlite_store_list_audit_filter_by_session(tmp_path: Path) -> None:
    from openminion.modules.controlplane.runtime.audit import AuditEvent

    store = SQLiteControlPlaneStore(tmp_path / "cp.db")
    store.put_audit(AuditEvent(event_type="ev", session_id="sess-A"))
    store.put_audit(AuditEvent(event_type="ev", session_id="sess-B"))
    store.put_audit(AuditEvent(event_type="ev", session_id="sess-A"))
    results = store.list_audit(session_id="sess-A")
    assert len(results) == 2
    store.close()


def test_sqlite_store_list_audit_filter_by_trace(tmp_path: Path) -> None:
    from openminion.modules.controlplane.runtime.audit import AuditEvent

    store = SQLiteControlPlaneStore(tmp_path / "cp.db")
    store.put_audit(AuditEvent(event_type="ev", trace_id="trace-1"))
    store.put_audit(AuditEvent(event_type="ev", trace_id="trace-2"))
    results = store.list_audit(trace_id="trace-1")
    assert len(results) == 1
    assert results[0]["trace_id"] == "trace-1"
    store.close()


def test_sqlite_store_reopened_preserves_data(tmp_path: Path) -> None:
    db = tmp_path / "cp.db"
    store = SQLiteControlPlaneStore(db)
    store.set_chat_binding("c1", "sess-persistent")
    store.close()
    store2 = SQLiteControlPlaneStore(db)
    rec = store2.get_chat_binding("c1")
    assert rec is not None
    assert rec["session_id"] == "sess-persistent"
    store2.close()


def test_sqlite_migration_applied_only_once(tmp_path: Path) -> None:
    db = tmp_path / "cp.db"
    store = SQLiteControlPlaneStore(db)
    count_before = store._conn.execute(
        "SELECT count(*) as n FROM cp_migrations"
    ).fetchone()["n"]
    store.close()
    store2 = SQLiteControlPlaneStore(db)
    count_after = store2._conn.execute(
        "SELECT count(*) as n FROM cp_migrations"
    ).fetchone()["n"]
    assert count_before == count_after
    store2.close()


# Audit — extended


def test_audit_event_to_dict_has_all_fields() -> None:
    from openminion.modules.controlplane.runtime.audit import AuditEvent

    ev = AuditEvent(
        event_type="controlplane.inbound.received",
        session_id="sess-1",
        trace_id="trace-1",
        details={"channel": "cli"},
    )
    d = ev.to_dict()
    assert d["event_type"] == "controlplane.inbound.received"
    assert d["session_id"] == "sess-1"
    assert d["trace_id"] == "trace-1"
    assert d["details"]["channel"] == "cli"
    assert "event_id" in d
    assert "timestamp" in d


def test_audit_logger_log_compat_shim() -> None:
    logger = AuditLogger()
    logger.log("inbound.received", channel="cli", session_id="s1")
    assert len(logger.events) == 1


def test_audit_logger_sink_failure_does_not_raise() -> None:

    def bad_sink(ev: object) -> None:
        raise RuntimeError("disk full")

    logger = AuditLogger(sink=bad_sink)
    # Should not raise
    logger.emit("controlplane.test")
    assert len(logger.events) == 1


def test_audit_logger_no_filter_returns_all() -> None:
    logger = AuditLogger()
    for i in range(5):
        logger.emit(f"event.{i}")
    assert len(logger.list_events()) == 5


# Runtime — extended multi-turn and multi-user


def test_runtime_multi_turn_same_session() -> None:
    rt, _, _ = _make_runtime()
    r1 = rt.handle_inbound(
        InboundMessage(user_key="u1", chat_key="c1", text="step one")
    )
    r2 = rt.handle_inbound(
        InboundMessage(user_key="u1", chat_key="c1", text="step two")
    )
    assert r1["session_id"] == r2["session_id"]
    assert "step one" in r1["text"]
    assert "step two" in r2["text"]


def test_runtime_two_users_fully_isolated() -> None:
    rt, _, _ = _make_runtime()
    r_alice = rt.handle_inbound(
        InboundMessage(user_key="alice", chat_key="chat:alice", text="hi")
    )
    r_bob = rt.handle_inbound(
        InboundMessage(user_key="bob", chat_key="chat:bob", text="hi")
    )
    assert r_alice["session_id"] != r_bob["session_id"]
    assert r_alice["agent_id"] == r_bob["agent_id"]  # both use default


def test_runtime_agent_switch_via_command() -> None:
    rt, outbound, _ = _make_runtime()
    rt.handle_inbound(
        InboundMessage(user_key="u1", chat_key="c1", text="/agent use agent:brain")
    )
    r = rt.handle_inbound(InboundMessage(user_key="u1", chat_key="c1", text="hello"))
    assert r["agent_id"] == "agent:brain"


def test_runtime_echo_brain_reflects_agent_id() -> None:
    rt, _, _ = _make_runtime()
    r = rt.handle_inbound(InboundMessage(user_key="u1", chat_key="c1", text="ping"))
    assert "agent:default" in r["text"]


def test_runtime_outbound_count_matches_inbound_count() -> None:
    rt, outbound, _ = _make_runtime()
    for i in range(5):
        rt.handle_inbound(InboundMessage(user_key="u1", chat_key="c1", text=f"msg {i}"))
    assert len(outbound) == 5


def test_runtime_command_then_chat_in_same_session() -> None:
    rt, outbound, _ = _make_runtime()
    rt.handle_inbound(InboundMessage(user_key="u1", chat_key="c1", text="/session id"))
    r = rt.handle_inbound(
        InboundMessage(user_key="u1", chat_key="c1", text="continue chatting")
    )
    assert r["type"] == "chat"
    assert outbound[-1]["type"] == "chat"
