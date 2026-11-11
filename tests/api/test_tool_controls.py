"""SDK tool boundaries through real runtime and local provider requests."""

import json
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event, Thread

import pytest
from pydantic import BaseModel, Field

from openminion import Agent, APIRuntime, Handoff, tool
from openminion.api.queries.runs import list_run_events, list_runs
from openminion.modules.tool.framework import build_registrar
from openminion.base.config import OpenMinionConfig, save_config
from openminion.base.config.core import ActionPolicyRuleConfig, ActionPolicyMatchConfig
from openminion.modules.policy.runtime.action_policy import derive_tool_risk_registry
from tests._csc_fixtures import _csc_install_default_agent


@pytest.fixture
def sdk_tools(tmp_path, monkeypatch):
    requests = []
    state = {"tool_sent": False, "unsolicited": False, "tool_name": "lookup_order"}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(request)
            names = [item["function"]["name"] for item in request.get("tools", [])]
            name = next(
                (
                    name
                    for name in names
                    if name
                    in {
                        state["tool_name"],
                        "transfer_to_child",
                        state.get("child_tool_name"),
                    }
                ),
                None,
            )
            if state["unsolicited"] and not names:
                name = "lookup_order"
            message = {"role": "assistant", "content": '{"answer":"LOCAL_MARKER"}'}
            if state.get("echo_tool_result"):
                results = [
                    m["content"] for m in request["messages"] if m["role"] == "tool"
                ]
                if results:
                    message["content"] = json.dumps({"answer": results[-1]})
            finish = "stop"
            sent_key = (
                "child_tool_sent"
                if name == state.get("child_tool_name")
                else "tool_sent"
            )
            if name and not state.get(sent_key, False):
                state[sent_key] = True
                arguments = (
                    state.get("tool_arguments", {"order_id": "A-104"})
                    if name != "transfer_to_child"
                    else {"message": "Return LOCAL_MARKER."}
                )
                message = {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "local-call",
                            "type": "function",
                            "function": {
                                "name": name,
                                "arguments": json.dumps(arguments),
                            },
                        }
                    ],
                }
                finish = "tool_calls"
            body = json.dumps(
                {
                    "id": "local-response",
                    "object": "chat.completion",
                    "model": "local",
                    "choices": [
                        {"index": 0, "message": message, "finish_reason": finish}
                    ],
                    "usage": {
                        "prompt_tokens": 5,
                        "completion_tokens": 5,
                        "total_tokens": 10,
                    },
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("LOCAL_SDK_KEY", "dummy-local")
    config = OpenMinionConfig()
    _csc_install_default_agent(config, provider="openai")
    config.providers.openai.api_key_env = "LOCAL_SDK_KEY"
    config.providers.openai.base_url = f"http://127.0.0.1:{server.server_port}/v1"
    config.action_policy.rules = [
        ActionPolicyRuleConfig(
            match=ActionPolicyMatchConfig(tool_name="lookup_order"), mode="ask"
        )
    ]
    config.runtime.process_mode = "single-process"
    config.runtime.daemon_auto_start = False
    config.storage.path = str(tmp_path / "state.db")
    config_path = tmp_path / "agents.json"
    save_config(config, str(config_path))
    runtime = APIRuntime.from_config_path(
        str(config_path), home_root=tmp_path, data_root=tmp_path / "data"
    )
    try:
        yield runtime, requests, state
    finally:
        runtime.close()
        server.shutdown()
        server.server_close()
        thread.join()


class Answer(BaseModel):
    answer: str


@pytest.mark.parametrize("tools", [None, []])
def test_empty_tools_differs_from_defaults(sdk_tools, tools):
    runtime, requests, _state = sdk_tools
    result = Agent(runtime=runtime, tools=tools, output_type=Answer).run("Say hello.")
    assert result.output.answer == "LOCAL_MARKER"
    execution_schemas = [
        t["function"]["name"]
        for request in requests
        for t in request.get("tools", [])
        if t["function"]["name"] != "submit_output"
    ]
    assert bool(execution_schemas) is (tools is None)


@pytest.mark.parametrize(
    "decision",
    ["allow", "async", "deny", "error", "cancel", "cancel_deny", "interactive"],
)
def test_action_policy_callback_controls_execution(sdk_tools, decision):
    runtime, _requests, _state = sdk_tools
    calls, approvals = [], []
    cancelled = Event()
    session_id = f"callback-policy-{decision}"

    @tool(dangerous=True)
    def lookup_order(order_id: str) -> str:
        calls.append(order_id)
        return "ready"

    def approve(name, args, approval_id):
        approvals.append((name, args, approval_id))
        if decision == "error":
            raise RuntimeError("fixture callback failed")
        if decision in {"cancel", "cancel_deny"}:
            cancelled.set()
        return decision in {"allow", "async", "cancel"}

    async def approve_async(*args):
        return approve(*args)

    callback = approve_async if decision == "async" else approve
    agent = Agent(
        runtime=runtime,
        session_id=session_id,
        tools=[lookup_order],
        forced_tools=["lookup_order"],
        output_type=Answer if decision in {"allow", "async"} else str,
    )
    if decision in {"cancel", "cancel_deny"}:
        with pytest.raises(RuntimeError, match="cancelled"):
            agent.run("Check A-104", approval_callback=approve, cancel_event=cancelled)
        runs = list_runs(None, session_id=session_id, runtime=runtime)["runs"]
        assert len(runs) == 1 and runs[0]["state"] == "failed"
        events = list_run_events(
            None, session_id=session_id, run_id=runs[0]["run_id"], runtime=runtime
        )["events"]
        assert events[-1]["event_type"] == "run.failed"
        assert events[-1]["payload"]["error_code"] == "CancelledError"
    else:
        result = agent.run(
            "Check A-104",
            approval_callback=None if decision == "interactive" else callback,
        )
        if decision in {"allow", "async"}:
            assert result.output.answer == "LOCAL_MARKER"
        if decision == "error":
            assert result.metadata["brain_status"] == "error"
            assert result.metadata["error_message"] == "fixture callback failed"
        if decision == "interactive":
            assert "CONFIG_RULE_ASK" in result.text
    assert len(approvals) == (0 if decision == "interactive" else 1)
    if approvals:
        assert approvals[0][0] == "lookup_order"
        assert approvals[0][1] == {"order_id": "A-104"}
        assert approvals[0][2]
    assert calls == (["A-104"] if decision in {"allow", "async"} else [])
    if decision in {"allow", "async"}:
        _state["tool_sent"] = False
        followup = Agent(
            runtime=runtime,
            session_id=session_id,
            tools=[lookup_order],
            forced_tools=["lookup_order"],
        )
        result = followup.run("Check A-104 again.")
        assert "CONFIG_RULE_ASK" in result.text
        assert len(approvals) == 1
        assert calls == ["A-104"]


@pytest.mark.parametrize("decision", ["allow_once", "deny", "cancel"])
def test_submitted_turn_delivers_policy_approval(sdk_tools, decision):
    runtime, _requests, _state = sdk_tools
    calls = []
    session_id = f"stream-policy-{decision}"

    @tool(dangerous=True)
    def lookup_order(order_id: str) -> str:
        calls.append(order_id)
        return "ready"

    build_registrar(lookup_order.tool_family_spec()).register(runtime.tools)
    runtime.tools.get("lookup_order").prompt_visible_runtime_name = True
    handle = runtime.submit_turn(
        payload={
            "message": "Check A-104",
            "session_id": session_id,
            "allowed_tools": ["lookup_order"],
            "forced_tools": ["lookup_order"],
            "timeout_seconds": 10,
        }
    )
    requested = []
    for chunk in handle.stream(timeout_s=15):
        if chunk.kind != "approval" or chunk.data["phase"] != "requested":
            continue
        requested.append(chunk.data)
        assert calls == []
        assert chunk.data["tool_name"] == "lookup_order"
        if decision == "cancel":
            assert handle.cancel()
        else:
            assert handle.resolve_approval(
                approval_id=chunk.data["approval_id"], decision=decision
            )
    response = handle.result(timeout_s=2)
    assert len(requested) == 1
    assert calls == (["A-104"] if decision == "allow_once" else [])
    assert not handle.resolve_approval(
        approval_id=requested[0]["approval_id"], decision="allow_once"
    )
    runs = list_runs(None, session_id=session_id, runtime=runtime)["runs"]
    assert len(runs) == 1
    events = list_run_events(
        None, session_id=session_id, run_id=runs[0]["run_id"], runtime=runtime
    )["events"]
    terminal_events = [
        event
        for event in events
        if event["event_type"] in {"run.completed", "run.failed", "run.cancelled"}
    ]
    assert terminal_events == [events[-1]]
    if decision == "cancel":
        assert response.final_text == ""
        assert response.errors and response.errors[0].code == "cancelled"
        assert runs[0]["state"] == "failed"
        assert events[-1]["event_type"] == "run.failed"
        assert events[-1]["payload"]["error_code"] == "CancelledError"
    else:
        assert response.errors == []
        assert runs[0]["state"] == "completed"
        assert events[-1]["event_type"] == "run.completed"


def test_submitted_turn_cancel_races_never_persist_completed_or_run_tool(
    sdk_tools, monkeypatch
):
    from openminion.services.runtime.manager import TurnHandle

    runtime, _requests, state = sdk_tools
    calls = []
    post_allow_cancels = []
    race_session = "stream-allow-then-cancel"

    @tool(dangerous=True)
    def lookup_order(order_id: str) -> str:
        calls.append(order_id)
        return "ready"

    build_registrar(lookup_order.tool_family_spec()).register(runtime.tools)
    runtime.tools.get("lookup_order").prompt_visible_runtime_name = True
    state["tool_sent"] = False

    base_request_approval = TurnHandle.request_approval

    def request_approval_then_cancel(handle: TurnHandle, **kwargs):
        approved = base_request_approval(handle, **kwargs)
        if approved and handle.session_id == race_session:
            post_allow_cancels.append(handle.cancel())
        return approved

    monkeypatch.setattr(TurnHandle, "request_approval", request_approval_then_cancel)
    race_handle = runtime.submit_turn(
        payload={
            "message": "Check A-104",
            "session_id": race_session,
            "allowed_tools": ["lookup_order"],
            "forced_tools": ["lookup_order"],
            "timeout_seconds": 10,
        }
    )
    requested = []
    for chunk in race_handle.stream(timeout_s=15):
        if chunk.kind == "approval" and chunk.data["phase"] == "requested":
            requested.append(chunk.data)
            assert race_handle.resolve_approval(
                approval_id=chunk.data["approval_id"], decision="allow_once"
            )
    race_response = race_handle.result(timeout_s=2)
    assert len(requested) == 1
    assert post_allow_cancels == [True]
    assert calls == []
    assert race_response.final_text == ""
    assert race_response.errors and race_response.errors[0].code == "cancelled"
    race_runs = list_runs(None, session_id=race_session, runtime=runtime)["runs"]
    assert len(race_runs) == 1 and race_runs[0]["state"] == "failed"
    events = list_run_events(
        None, session_id=race_session, run_id=race_runs[0]["run_id"], runtime=runtime
    )["events"]
    terminal_events = [
        event
        for event in events
        if event["event_type"] in {"run.completed", "run.failed", "run.cancelled"}
    ]
    assert terminal_events == [events[-1]]
    assert events[-1]["event_type"] == "run.failed"
    assert events[-1]["payload"]["error_code"] == "CancelledError"


@pytest.mark.parametrize("forced", [False, True])
def test_empty_tools_blocks_unsolicited_provider_calls(sdk_tools, forced):
    runtime, requests, state = sdk_tools
    calls = []

    @tool
    def lookup_order(order_id: str) -> str:
        calls.append(order_id)
        return "ready"

    build_registrar(lookup_order.tool_family_spec()).register(runtime.tools)
    runtime.tools.get("lookup_order").prompt_visible_runtime_name = True
    state["unsolicited"] = True
    Agent(
        runtime=runtime, tools=[], forced_tools=["lookup_order"] if forced else []
    ).run("Check A-104")
    assert calls == []
    assert all(
        t["function"]["name"] == "submit_output"
        for request in requests
        for t in request.get("tools", [])
    )


def test_explicit_handoff_remains_available_with_empty_tools(sdk_tools):
    runtime, requests, _state = sdk_tools
    child = Agent(runtime=runtime, name="child", tools=[])
    parent = Agent(runtime=runtime, tools=[], handoffs=[Handoff(target=child)])
    parent.run("Say hello.", approval_callback=lambda *_args: True)
    assert any(
        t["function"]["name"] == "transfer_to_child"
        for request in requests
        for t in request.get("tools", [])
    )


@pytest.mark.parametrize("callback", [None, "allow", "deny"])
@pytest.mark.parametrize("rule", [None, "ask", "block"])
@pytest.mark.parametrize(
    "kind", ["format_report", "lookup_order", "dangerous", "handoff"]
)
def test_declared_tools_enforce_policy_and_approval(sdk_tools, callback, rule, kind):
    runtime, requests, state = sdk_tools
    calls, approvals = [], []
    name = "transfer_to_child" if kind == "handoff" else kind
    state.update(tool_name=name, echo_tool_result=True)
    runtime.config.action_policy.rules = (
        [
            ActionPolicyRuleConfig(
                match=ActionPolicyMatchConfig(tool_name=name), mode=rule
            )
        ]
        if rule
        else []
    )

    @tool(
        name=name,
        min_scope="READ_ONLY",
        idempotent=True,
        dangerous=kind == "dangerous",
    )
    def handler(order_id: str) -> str:
        calls.append(order_id)
        return "HANDLER_RESULT"

    child = Agent(runtime=runtime, name="child", tools=[])
    agent = Agent(
        runtime=runtime,
        tools=[] if kind == "handoff" else [handler],
        handoffs=[Handoff(target=child)] if kind == "handoff" else [],
    )

    def approve(tool_name, args, approval_id):
        approvals.append((tool_name, args, approval_id))
        return callback == "allow"

    result = agent.run(
        "Check A-104",
        approval_callback=approve if callback else None,
    )
    needs_approval = rule == "ask" or kind in {"dangerous", "handoff"}
    should_run = rule != "block" and (not needs_approval or callback == "allow")
    assert len(approvals) == int(bool(callback) and needs_approval and rule != "block")
    if approvals:
        assert approvals[0][0] == name
        assert approvals[0][1] == (
            {"message": "Return LOCAL_MARKER."}
            if kind == "handoff"
            else {"order_id": "A-104"}
        )
        assert approvals[0][2]
    child_requests = [
        request
        for request in requests
        if not request.get("tools")
        and any(
            m.get("role") == "user" and "Return LOCAL_MARKER." in str(m.get("content"))
            for m in request["messages"]
        )
    ]
    if kind == "handoff":
        assert bool(child_requests) is should_run
        if should_run:
            assert "LOCAL_MARKER" in result.text
    else:
        assert calls == (["A-104"] if should_run else [])
        if should_run:
            assert "HANDLER_RESULT" in result.text
    if needs_approval and callback is None and rule != "block":
        assert (
            f"Risk: {'write' if kind == 'handoff' else 'destructive' if kind == 'dangerous' else 'read'}"
            in result.text
        )
        if kind == "handoff":
            assert "Side effects: local" in result.text
            assert "Reversibility: unknown" in result.text
    if rule == "block":
        assert "configured exact-tool rule" in result.text


def test_handoff_approval_does_not_authorize_child_tools(sdk_tools):
    runtime, _requests, state = sdk_tools
    calls, approvals = [], []
    state.update(
        tool_name="transfer_to_child",
        child_tool_name="lookup_order",
        echo_tool_result=True,
    )
    runtime.config.action_policy.rules = []

    @tool(dangerous=True)
    def lookup_order(order_id: str) -> str:
        calls.append(order_id)
        return "CHILD_HANDLER_RESULT"

    build_registrar(lookup_order.tool_family_spec()).register(runtime.tools)
    spec = runtime.tools.get("lookup_order")
    spec.prompt_visible_runtime_name = True
    for key, risk in derive_tool_risk_registry({"lookup_order": spec}).items():
        runtime.action_policy.register_risk(key, risk)
    child = Agent(
        runtime=runtime,
        name="child",
        tools=["lookup_order"],
    )

    def approve(name, _args, _approval_id):
        approvals.append(name)
        return True

    result = Agent(runtime=runtime, tools=[], handoffs=[Handoff(target=child)]).run(
        "delegate",
        approval_callback=approve,
    )
    assert approvals == ["transfer_to_child"]
    assert calls == []
    assert "Risk: destructive" in result.text


class _Window(BaseModel):
    start: int
    end: int


@dataclass
class _Limits:
    max_items: int
    strict: bool = False


def test_sdk_tool_receives_structured_arguments_as_instances(sdk_tools):
    runtime, _requests, state = sdk_tools
    runtime.config.action_policy.rules = []
    state.update(
        tool_name="summarize_window",
        echo_tool_result=True,
        tool_arguments={"window": {"start": 1, "end": 4}, "limits": {"max_items": 3}},
    )
    received = []

    @tool(name="summarize_window", min_scope="READ_ONLY", idempotent=True)
    def summarize_window(window: _Window, limits: _Limits, label: str = "all") -> str:
        received.append((window, limits, label))
        return f"WINDOW {window.start}-{window.end} max={limits.max_items} {label}"

    result = Agent(runtime=runtime, tools=[summarize_window]).run("Summarize.")

    assert received == [(_Window(start=1, end=4), _Limits(max_items=3), "all")]
    assert type(received[0][0]) is _Window
    assert type(received[0][1]) is _Limits
    assert "WINDOW 1-4 max=3 all" in result.text


class _AliasedFilter(BaseModel):
    min_total: int = Field(alias="minTotal", ge=0)
    status: str = "open"


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Shared runtime, not the @tool handler: tool-command sanitizing "
        "replaces the model's arguments with the args model's canonical "
        "model_dump(), and the brain tool adapter then re-validates that "
        "mapping alias-only, so an aliased argument is rejected as "
        "INVALID_ARGUMENT before the handler runs. Pre-existing on dev; "
        "tracked in https://github.com/openminion/openminion/issues/454 "
        "(#454). A pass here does not mean the SDK alias path is fixed "
        "until #454 is resolved."
    ),
)
def test_sdk_tool_receives_nested_alias_model_argument(sdk_tools):
    runtime, _requests, state = sdk_tools
    runtime.config.action_policy.rules = []
    state.update(
        tool_name="filter_orders",
        echo_tool_result=True,
        tool_arguments={"query": {"minTotal": 7}},
    )
    received = []

    @tool(name="filter_orders", min_scope="READ_ONLY", idempotent=True)
    def filter_orders(query: _AliasedFilter) -> str:
        received.append(query)
        return f"FILTER min={query.min_total} status={query.status}"

    result = Agent(runtime=runtime, tools=[filter_orders]).run("Filter.")

    assert received == [_AliasedFilter(minTotal=7)]
    assert type(received[0]) is _AliasedFilter
    assert "FILTER min=7 status=open" in result.text
