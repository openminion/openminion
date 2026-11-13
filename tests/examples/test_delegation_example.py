"""Delegation example: handoff approval controls at the CLI and runtime layers.

The CLI output is checked with a scripted recorder; the pause, denial and
allow controls run through the real `APIRuntime` using the approval-test
fixtures from `tests/runtime/test_turn_capture_authorization.py`.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from runpy import run_path
from types import SimpleNamespace
from typing import Any, ClassVar, Self, cast

import pytest

from openminion import Agent, Handoff
from openminion.api.handoff import build_delegate_family_spec
from openminion.base.config import OpenMinionConfig
from openminion.modules.policy.runtime.action_policy import (
    build_action_policy_service,
)
from openminion.modules.tool.framework import build_registrar
from openminion.modules.tool.registry import ToolRegistry
from tests.runtime.test_turn_capture_authorization import (
    AGENT_ID,
    REPLY,
    _call,
    _completion,
    _Provider,
    _runtime,
)

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "examples" / "sdk" / "delegation.py"
HANDOFF_TOOL = "transfer_to_researcher"
POLICY_PROMPT = (
    "Policy confirmation required.\n"
    f"{HANDOFF_TOOL}\n"
    "Risk: write\n"
    "Side effects: local\n"
    "Reversibility: unknown\n"
    "Reason: UNKNOWN_REVERSIBILITY"
)
DENIED_REPLY = "Confirmation denied. The action was not run."


class _RecorderAgent:
    """Stand-in Agent that records runs and scripted coordinator replies."""

    instances: ClassVar[list[_RecorderAgent]] = []

    def __init__(self, **kwargs: Any) -> None:
        self.name = kwargs["name"]
        self.handoffs = list(kwargs.get("handoffs") or [])
        self.runs: list[str] = []
        self.captured_approval_callback = None
        _RecorderAgent.instances.append(self)

    def run(self, message: str, **kwargs: Any) -> SimpleNamespace:
        self.runs.append(message)
        self.captured_approval_callback = kwargs.get("approval_callback")
        if self.name != "coordinator":
            return SimpleNamespace(
                text=f"researcher reply to {message!r}",
                metadata={},
                thread_id=None,
            )
        callback = kwargs.get("approval_callback")
        if callback is None:
            return SimpleNamespace(
                text=POLICY_PROMPT,
                metadata={"respond_kind": "policy_confirmation_prompt"},
                thread_id="thread-1",
            )
        approved = callback(HANDOFF_TOOL, {"message": message}, "approval-1")
        if not approved:
            return SimpleNamespace(
                text=DENIED_REPLY,
                metadata={"respond_kind": "policy_confirmation_prompt"},
                thread_id="thread-1",
            )
        target = self.handoffs[0].target
        answer = target.run(message)
        return SimpleNamespace(text=answer.text, metadata={}, thread_id=None)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args: object) -> None:
        return None


def _load_module(monkeypatch):
    _RecorderAgent.instances.clear()
    module = run_path(str(SCRIPT))
    monkeypatch.setitem(module["main"].__globals__, "Agent", _RecorderAgent)
    return module


def _run_main(module, argv: list[str]) -> int:
    old_argv = sys.argv
    sys.argv = ["delegation.py", *argv]
    try:
        return module["main"]()
    finally:
        sys.argv = old_argv


def _agents_named(name: str) -> list[_RecorderAgent]:
    return [agent for agent in _RecorderAgent.instances if agent.name == name]


def test_handoff_pause_reports_prompt_not_child_answer(monkeypatch, capsys) -> None:
    module = _load_module(monkeypatch)
    assert _run_main(module, ["--mode", "handoff", "--approval", "pause"]) == 0

    coordinator = _agents_named("coordinator")[0]
    assert coordinator.captured_approval_callback is None
    assert _agents_named("researcher")[0].runs == []
    out = capsys.readouterr().out
    assert "paused: the policy asks the operator" in out
    assert "not the researcher's answer" in out
    assert POLICY_PROMPT in out
    assert "This script ends here." in out
    assert "thread_id='thread-1'" in out


def test_handoff_deny_ends_without_running_target(monkeypatch, capsys) -> None:
    module = _load_module(monkeypatch)
    assert _run_main(module, ["--mode", "handoff", "--approval", "deny"]) == 0

    assert _agents_named("researcher")[0].runs == []
    out = capsys.readouterr().out
    assert "operator: denying handoff of 'Review this launch plan.'" in out
    assert DENIED_REPLY in out
    assert "denied: the operator refused the handoff" in out
    assert "paused:" not in out
    assert "agent.run(" not in out


def test_handoff_allow_once_runs_target_once(monkeypatch, capsys) -> None:
    module = _load_module(monkeypatch)
    assert _run_main(module, ["--mode", "handoff", "--approval", "allow_once"]) == 0

    assert _agents_named("researcher")[0].runs == ["Review this launch plan."]
    out = capsys.readouterr().out
    assert "operator: allowing handoff of 'Review this launch plan.'" in out
    assert "researcher reply to 'Review this launch plan.'" in out


def test_operator_decision_is_scoped_and_one_shot(capsys) -> None:
    module = run_path(str(SCRIPT))
    decide = module["operator_decision"]("allow_once")

    assert decide("exec.run", {"command": "ls"}, "approval-0") is False
    assert decide(HANDOFF_TOOL, {"message": "hi"}, "approval-1") is True
    assert decide(HANDOFF_TOOL, {"message": "again"}, "approval-2") is False
    assert module["operator_decision"]("deny")(HANDOFF_TOOL, {}, "approval-3") is False
    capsys.readouterr()


def test_handoff_policy_pauses_on_unknown_reversibility() -> None:
    class Peer:
        name = "researcher"
        instructions = "Research one bounded question."

        def run(self, message: str) -> str:
            raise AssertionError("target must not run before confirmation")

    registry = ToolRegistry()
    family = build_delegate_family_spec([Handoff(target=cast(Any, Peer()))])
    assert family is not None
    build_registrar(family).register(registry)
    with tempfile.TemporaryDirectory() as data_root:
        policy = build_action_policy_service(
            config=OpenMinionConfig(),
            tool_registry=registry,
            data_root=Path(data_root),
        )
        try:
            result = policy.check(
                {
                    "tool": HANDOFF_TOOL,
                    "method": "default",
                    "args": {"message": "hi"},
                },
                {},
            )
        finally:
            policy.close()

    assert result.decision == "REQUIRE_CONFIRM"
    assert result.reason_code == "UNKNOWN_REVERSIBILITY"
    assert (result.risk.risk_class, result.risk.reversibility) == (
        "write",
        "unknown",
    )


class _HandoffProvider(_Provider):
    """Scripted provider: the coordinator hands off once, everyone answers."""

    def __init__(self) -> None:
        super().__init__()
        self.handed_off = False

    def respond(self, body: dict[str, Any]) -> dict[str, Any]:
        names = [tool["function"]["name"] for tool in body.get("tools") or []]
        choice = body.get("tool_choice")
        forced = isinstance(choice, dict) and (
            (choice.get("function") or {}).get("name") == "submit_output"
        )
        if not forced and HANDOFF_TOOL in names and not self.handed_off:
            self.handed_off = True
            return _call(HANDOFF_TOOL, {"message": "Research this launch plan."})
        return _completion({"role": "assistant", "content": REPLY}, "stop")


def _handoff_turn(runtime: Any, callback: Any) -> tuple[Any, list[str]]:
    target_runs: list[str] = []

    def recording_run(message: str, **_kwargs: Any) -> Any:
        target_runs.append(message)
        return original_run(message)

    with Agent(
        runtime=runtime,
        name="researcher",
        instructions="Research one bounded question and return concise findings.",
    ) as researcher:
        original_run = researcher.run
        researcher.run = recording_run  # type: ignore[method-assign]
        with Agent(
            runtime=runtime,
            name="coordinator",
            instructions="Delegate research questions to the researcher.",
            handoffs=[Handoff(target=researcher)],
            forced_tools=[HANDOFF_TOOL],
        ) as coordinator:
            result = coordinator.run(
                "Research this launch plan.", approval_callback=callback
            )
    assert result.run_state == "completed"
    return result, target_runs


def test_runtime_pause_leaves_target_unrun(tmp_path, monkeypatch) -> None:
    provider = _HandoffProvider()
    runtime = _runtime(tmp_path, monkeypatch, provider, constraint=None)
    try:
        result, target_runs = _handoff_turn(runtime, None)
    finally:
        runtime.close()

    assert provider.handed_off is True
    assert result.text.startswith(f"{AGENT_ID}: Policy confirmation required.")
    assert result.metadata.get("respond_kind") == "policy_confirmation_prompt"
    assert target_runs == []


def _asking_wrapper(callback, asked):
    def asking(tool_name: str, args: dict[str, Any], approval_id: str) -> bool:
        asked.append(tool_name)
        return callback(tool_name, args, approval_id)

    return asking


def test_runtime_denial_runs_no_target(tmp_path, monkeypatch, capsys) -> None:
    module = run_path(str(SCRIPT))
    provider = _HandoffProvider()
    runtime = _runtime(tmp_path, monkeypatch, provider, constraint=None)
    asked: list[str] = []
    callback = _asking_wrapper(module["operator_decision"]("deny"), asked)
    try:
        result, target_runs = _handoff_turn(runtime, callback)
    finally:
        runtime.close()

    assert provider.handed_off is True
    assert asked == [HANDOFF_TOOL]
    assert result.text.startswith(f"{AGENT_ID}: {DENIED_REPLY}")
    assert target_runs == []
    assert result.metadata.get("respond_kind") == "policy_confirmation_prompt"
    capsys.readouterr()
    module["_print_handoff"](result, "deny")
    out = capsys.readouterr().out
    assert "denied: the operator refused the handoff" in out
    assert "paused:" not in out
    assert "agent.run(" not in out


class _AnswerOnlyProvider(_Provider):
    """Scripted provider: the coordinator answers directly and never hands off."""

    def respond(self, body: dict[str, Any]) -> dict[str, Any]:
        return _completion({"role": "assistant", "content": REPLY}, "stop")


@pytest.mark.parametrize("approval", ["deny", "allow_once", "pause"])
def test_runtime_answer_without_confirmation_prints_unchanged(
    tmp_path, monkeypatch, capsys, approval
) -> None:
    module = run_path(str(SCRIPT))
    provider = _AnswerOnlyProvider()
    runtime = _runtime(tmp_path, monkeypatch, provider, constraint=None)
    asked: list[str] = []
    callback = (
        None
        if approval == "pause"
        else _asking_wrapper(module["operator_decision"](approval), asked)
    )
    try:
        result, target_runs = _handoff_turn(runtime, callback)
    finally:
        runtime.close()

    assert result.metadata.get("respond_kind") != "policy_confirmation_prompt"
    assert asked == []
    assert target_runs == []
    capsys.readouterr()
    assert module["_print_handoff"](result, approval) == 0
    assert capsys.readouterr().out == f"{result.text}\n"


def test_runtime_allow_once_runs_target_exactly_once(tmp_path, monkeypatch) -> None:
    module = run_path(str(SCRIPT))
    provider = _HandoffProvider()
    runtime = _runtime(tmp_path, monkeypatch, provider, constraint=None)
    asked: list[str] = []
    callback = _asking_wrapper(module["operator_decision"]("allow_once"), asked)
    try:
        result, target_runs = _handoff_turn(runtime, callback)
    finally:
        runtime.close()

    assert provider.handed_off is True
    assert asked == [HANDOFF_TOOL]
    assert target_runs == ["Research this launch plan."]
    assert result.text.startswith(f"{AGENT_ID}: ")
