from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any

import pytest

from openminion.services.agent.hooks import Hook, HookContext
from openminion.services.agent.lifecycle import (
    LIFECYCLE_EVENT_ON_ERROR,
    LIFECYCLE_EVENT_ON_SUBAGENT_STOP,
    LIFECYCLE_EVENT_POST_TOOL_USE,
    LIFECYCLE_EVENT_PRE_TOOL_USE,
    LIFECYCLE_EVENT_SESSION_START,
    LIFECYCLE_EVENT_SESSION_STOP,
    LIFECYCLE_EVENT_TYPES,
    LifecycleEvent,
    LifecycleHookRegistry,
    fire_lifecycle_event,
    get_default_lifecycle_registry,
    reset_default_lifecycle_registry,
)


@pytest.fixture(autouse=True)
def _reset_default_registry():
    reset_default_lifecycle_registry()
    yield
    reset_default_lifecycle_registry()


def test_six_canonical_event_types_exposed() -> None:
    assert LIFECYCLE_EVENT_TYPES == frozenset(
        {
            LIFECYCLE_EVENT_PRE_TOOL_USE,
            LIFECYCLE_EVENT_POST_TOOL_USE,
            LIFECYCLE_EVENT_SESSION_START,
            LIFECYCLE_EVENT_SESSION_STOP,
            LIFECYCLE_EVENT_ON_ERROR,
            LIFECYCLE_EVENT_ON_SUBAGENT_STOP,
        }
    )
    assert LIFECYCLE_EVENT_PRE_TOOL_USE == "pre_tool_use"
    assert LIFECYCLE_EVENT_POST_TOOL_USE == "post_tool_use"
    assert LIFECYCLE_EVENT_SESSION_START == "session_start"
    assert LIFECYCLE_EVENT_SESSION_STOP == "session_stop"
    assert LIFECYCLE_EVENT_ON_ERROR == "on_error"
    assert LIFECYCLE_EVENT_ON_SUBAGENT_STOP == "on_subagent_stop"


def test_lifecycle_event_default_safe_fields() -> None:
    event = LifecycleEvent(event_type=LIFECYCLE_EVENT_PRE_TOOL_USE)
    assert event.event_type == "pre_tool_use"
    assert event.timestamp_ms == 0
    assert event.tool_name == ""
    assert event.tool_args == {}
    assert event.tool_call_id == ""
    assert event.tool_ok is None
    assert event.tool_duration_ms is None
    assert event.error_message == ""
    assert event.subagent_id == ""
    assert event.source_payload == {}


def test_lifecycle_event_carries_populated_fields() -> None:
    event = LifecycleEvent(
        event_type=LIFECYCLE_EVENT_POST_TOOL_USE,
        trace_id="trace-1",
        session_id="sess-1",
        tool_name="bash",
        tool_args={"command": "ls"},
        tool_call_id="c1",
        tool_ok=True,
        tool_duration_ms=42,
        tool_content="ok",
    )
    assert event.tool_args == {"command": "ls"}
    assert event.tool_duration_ms == 42


def test_registry_default_empty() -> None:
    reg = LifecycleHookRegistry()
    assert reg.count() == 0
    for et in LIFECYCLE_EVENT_TYPES:
        assert reg.count(et) == 0


def test_register_unknown_event_type_raises() -> None:
    reg = LifecycleHookRegistry()
    with pytest.raises(ValueError, match="unknown lifecycle event type"):
        reg.register("not_a_real_event", lambda e, c: None)


def test_register_non_callable_raises() -> None:
    reg = LifecycleHookRegistry()
    with pytest.raises(TypeError, match="must be callable"):
        reg.register(LIFECYCLE_EVENT_PRE_TOOL_USE, "not a callable")  # type: ignore[arg-type]


def test_register_and_fire_dispatches_per_event_type() -> None:
    reg = LifecycleHookRegistry()
    received_pre: list[str] = []
    received_post: list[str] = []
    reg.register(
        LIFECYCLE_EVENT_PRE_TOOL_USE, lambda e, c: received_pre.append(e.tool_name)
    )
    reg.register(
        LIFECYCLE_EVENT_POST_TOOL_USE, lambda e, c: received_post.append(e.tool_name)
    )
    ctx = HookContext(config=None, logger=logging.getLogger("test"))  # type: ignore[arg-type]
    reg.fire(
        LifecycleEvent(event_type=LIFECYCLE_EVENT_PRE_TOOL_USE, tool_name="bash"), ctx
    )
    reg.fire(
        LifecycleEvent(event_type=LIFECYCLE_EVENT_POST_TOOL_USE, tool_name="git"), ctx
    )
    assert received_pre == ["bash"]
    assert received_post == ["git"]


def test_fire_event_with_no_registered_hooks_is_noop() -> None:
    reg = LifecycleHookRegistry()
    ctx = HookContext(config=None, logger=logging.getLogger("test"))  # type: ignore[arg-type]
    reg.fire(LifecycleEvent(event_type=LIFECYCLE_EVENT_PRE_TOOL_USE), ctx)


def test_fire_empty_event_type_drops_silently() -> None:
    reg = LifecycleHookRegistry()
    received: list[Any] = []
    reg.register(LIFECYCLE_EVENT_PRE_TOOL_USE, lambda e, c: received.append(e))
    ctx = HookContext(config=None, logger=logging.getLogger("test"))  # type: ignore[arg-type]
    reg.fire(LifecycleEvent(event_type=""), ctx)
    assert received == []


def test_misbehaving_hook_does_not_break_other_hooks(caplog) -> None:
    reg = LifecycleHookRegistry()
    fired_after_bad: list[str] = []

    def _bad_hook(e, c):
        raise RuntimeError("boom")

    def _good_hook(e, c):
        fired_after_bad.append(e.tool_name)

    reg.register(LIFECYCLE_EVENT_PRE_TOOL_USE, _bad_hook)
    reg.register(LIFECYCLE_EVENT_PRE_TOOL_USE, _good_hook)
    ctx = HookContext(config=None, logger=logging.getLogger("test"))  # type: ignore[arg-type]
    with caplog.at_level(logging.ERROR):
        reg.fire(
            LifecycleEvent(event_type=LIFECYCLE_EVENT_PRE_TOOL_USE, tool_name="bash"),
            ctx,
        )
    assert fired_after_bad == ["bash"]
    assert any("lifecycle hook raised" in rec.message for rec in caplog.records)


def test_unregister_removes_hook() -> None:
    reg = LifecycleHookRegistry()
    received: list[Any] = []
    hook = lambda e, c: received.append(e)  # noqa: E731
    reg.register(LIFECYCLE_EVENT_PRE_TOOL_USE, hook)
    assert reg.count(LIFECYCLE_EVENT_PRE_TOOL_USE) == 1
    assert reg.unregister(LIFECYCLE_EVENT_PRE_TOOL_USE, hook) is True
    assert reg.count(LIFECYCLE_EVENT_PRE_TOOL_USE) == 0
    assert reg.unregister(LIFECYCLE_EVENT_PRE_TOOL_USE, hook) is False


def test_reset_clears_all_hooks() -> None:
    reg = LifecycleHookRegistry()
    reg.register(LIFECYCLE_EVENT_PRE_TOOL_USE, lambda e, c: None)
    reg.register(LIFECYCLE_EVENT_POST_TOOL_USE, lambda e, c: None)
    assert reg.count() == 2
    reg.reset()
    assert reg.count() == 0


def test_hook_return_value_is_ignored_v1() -> None:
    reg = LifecycleHookRegistry()
    received: list[Any] = []

    def _hook_that_returns_block(e, c):
        received.append(("block", e.tool_name))
        return {"block": True}  # ignored

    def _hook_that_returns_mutated(e, c):
        received.append(("mutate", e.tool_name))
        return {"mutate": {"tool_name": "git"}}  # ignored

    reg.register(LIFECYCLE_EVENT_PRE_TOOL_USE, _hook_that_returns_block)
    reg.register(LIFECYCLE_EVENT_PRE_TOOL_USE, _hook_that_returns_mutated)
    ctx = HookContext(config=None, logger=logging.getLogger("test"))  # type: ignore[arg-type]
    reg.fire(
        LifecycleEvent(event_type=LIFECYCLE_EVENT_PRE_TOOL_USE, tool_name="bash"), ctx
    )
    assert received == [("block", "bash"), ("mutate", "bash")]


def test_default_registry_lazy_init() -> None:
    reg = get_default_lifecycle_registry()
    assert isinstance(reg, LifecycleHookRegistry)
    assert get_default_lifecycle_registry() is reg


def test_fire_lifecycle_event_dispatches_to_default_registry() -> None:
    received: list[str] = []
    reg = get_default_lifecycle_registry()
    reg.register(
        LIFECYCLE_EVENT_PRE_TOOL_USE, lambda e, c: received.append(e.tool_name)
    )
    fire_lifecycle_event(
        LifecycleEvent(event_type=LIFECYCLE_EVENT_PRE_TOOL_USE, tool_name="bash")
    )
    assert received == ["bash"]


def test_fire_lifecycle_event_fast_noop_when_no_hooks() -> None:
    # Empty registry; should not raise even with a malformed
    # config (we'd hit the context resolver if the short-circuit
    # broke).
    fire_lifecycle_event(LifecycleEvent(event_type=LIFECYCLE_EVENT_PRE_TOOL_USE))


def test_reset_default_lifecycle_registry_clears_state() -> None:
    reg = get_default_lifecycle_registry()
    reg.register(LIFECYCLE_EVENT_PRE_TOOL_USE, lambda e, c: None)
    assert reg.count() == 1
    reset_default_lifecycle_registry()
    new_reg = get_default_lifecycle_registry()
    assert new_reg.count() == 0
    assert new_reg is not reg


def test_message_hook_class_behavior_unchanged() -> None:
    hook = Hook()
    assert hook.name == "hook"
    assert hook.inbound_hook_mode == "mutating"
    assert hook.outbound_hook_mode == "mutating"
    msg = SimpleNamespace(body="hello", channel="x", target="y", metadata={})
    ctx = HookContext(config=None, logger=logging.getLogger("test"))  # type: ignore[arg-type]
    assert hook.on_message(msg, ctx) is msg


def test_action_dispatch_imports_lifecycle_helpers() -> None:
    import openminion.modules.brain.tools.action_dispatch as dispatch_mod

    src = open(dispatch_mod.__file__, encoding="utf-8").read()
    assert "LIFECYCLE_EVENT_PRE_TOOL_USE" in src, (
        "pre_tool_use firing site missing in action_dispatch.py"
    )
    assert "LIFECYCLE_EVENT_POST_TOOL_USE" in src, (
        "post_tool_use firing site missing in action_dispatch.py"
    )
    assert "fire_lifecycle_event" in src, (
        "fire_lifecycle_event helper not invoked from action_dispatch.py"
    )
