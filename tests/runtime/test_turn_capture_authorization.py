"""A turn's own memory capture asks the authorization that recovery asks (#448).

Each test builds a real `APIRuntime` for one agent whose `openai` provider is a
scripted OpenAI-compatible server on 127.0.0.1. Unless a test says otherwise,
the agent has an identity profile (`profile.yaml` under `identity.bundle_root`)
with one hard constraint. SDK turns ask the agent to remember a preference; the
provider answers the auto fact extraction with one `user_preference` item and
records the kind of every request.

The tests record the agent security policy's `memory.write` decisions and the
memctl bundle writes (both call the original), and read the memory store, the
turn's capture intent and its `memory.capture.result`. Recovery runs through
the real `CronTurnExecutor` with the real `memory_capture_recovery_allowed`.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from openminion import Agent, APIRuntime
from openminion.base.config import (
    AgentProfileConfig,
    ConfigManager,
    OpenMinionConfig,
    save_config,
)
from openminion.base.types import Message
from openminion.modules.policy.adapters.memory import memory_capture_recovery_allowed
from openminion.modules.session.capture import build_capture_identity
from openminion.services.agent.execution.composition import AgentServiceTurnFlowAdapter
from openminion.services.runtime.cron.executor import CronTurnExecutor
from openminion.services.runtime.daemon import build_turn_request
from openminion.tools.task.constants import CONSOLIDATION_PAYLOAD_KEY
from tests._csc_fixtures import _csc_install_default_agent

AGENT_ID = "openminion"
WRITE_CONSTRAINT = "NEVER write memory records"
NEUTRAL_CONSTRAINT = "NEVER reveal secrets"
MESSAGE = "Please remember that I prefer tabs over spaces for indentation."
REPLY = "Noted: tabs over spaces."
CAPTURE_ITEM = {
    "kind": "user_preference",
    "normalized_key": "user_preference:indentation",
    "title": "Prefers tabs for indentation",
    "content": "The user prefers tabs over spaces for indentation.",
    "tags": [],
}
TOOL_ARGS = {
    "record_type": "fact",
    "key": "fact:indentation",
    "title": "Indentation",
    "content": "The user prefers tabs.",
}
UNAUTHORIZED = "CAPTURE_RECOVERY_UNAUTHORIZED"
PROFILE_YAML = """agent_id: openminion
display_name: Capture authorization agent
profile_revision: 1
role:
  mission: Answer the user briefly.
  hard_constraints:
    - {constraint}
personality:
  tone: direct
  verbosity: terse
risk:
  risk_level: low
tool_posture:
  tool_use: allowed
"""


def _completion(message: dict[str, Any], finish: str) -> dict[str, Any]:
    return {
        "id": "local",
        "object": "chat.completion",
        "model": "local",
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10},
    }


def _call(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    call = {
        "id": f"call-{name}",
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }
    return _completion(
        {"role": "assistant", "content": None, "tool_calls": [call]}, "tool_calls"
    )


def _text(body: dict[str, Any]) -> str:
    parts = []
    for message in body.get("messages") or []:
        content = message.get("content")
        if isinstance(content, list):
            content = " ".join(str(part.get("text", "")) for part in content)
        parts.append(str(content or ""))
    return "\n".join(parts)


class _Provider:
    """Scripted OpenAI-compatible server; records the kind of each request."""

    def __init__(self) -> None:
        self.requests: list[str] = []
        self.write_tool_call = False
        self._lock = threading.Lock()
        provider = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers["Content-Length"])
                body = json.loads(self.rfile.read(length))
                payload = json.dumps(provider.respond(body)).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *_args: Any) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}/v1"

    def respond(self, body: dict[str, Any]) -> dict[str, Any]:
        text = _text(body)
        names = [tool["function"]["name"] for tool in body.get("tools") or []]
        choice = body.get("tool_choice")
        forced = isinstance(choice, dict) and (
            (choice.get("function") or {}).get("name") == "submit_output"
        )
        with self._lock:
            if forced and "purpose: reflect" in text:
                kind = "extraction"
            elif forced and "freshness-routing classifier" in text:
                kind = "freshness"
            elif forced:
                kind = "structured"
            elif (
                self.write_tool_call
                and "memory_write" in names
                and "tool_call" not in self.requests
            ):
                kind = "tool_call"
            else:
                kind = "text"
            self.requests.append(kind)
        if kind == "extraction":
            items = [dict(CAPTURE_ITEM)] if MESSAGE in text else []
            return _call("submit_output", {"items": items})
        if kind == "freshness":
            return _call(
                "submit_output",
                {
                    "intent": "statement",
                    "domain": "general",
                    "time_sensitive": False,
                    "needs_live_data": False,
                    "needs_sources": False,
                    "needs_exact_date": False,
                    "answer_mode": "direct",
                    "reason": "No live data needed.",
                    "confidence": 1,
                },
            )
        if kind == "structured":
            if "purpose: judge" in text or "[JUDGE CONTEXT]" in text:
                return _call(
                    "submit_output",
                    {"final_answer": REPLY, "satisfied": True, "next_action": "close"},
                )
            return _call("submit_output", {})
        if kind == "tool_call":
            return _call("memory_write", TOOL_ARGS)
        return _completion({"role": "assistant", "content": REPLY}, "stop")

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


@pytest.fixture
def provider() -> Any:
    server = _Provider()
    try:
        yield server
    finally:
        server.close()


def _write_profile(root: Path, constraint: str) -> None:
    path = root / "identity" / AGENT_ID / "profile.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(PROFILE_YAML.format(constraint=constraint), encoding="utf-8")


def _runtime(
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    provider: _Provider,
    *,
    constraint: str | None,
    internal_actor_scopes: tuple[str, ...] = (),
    extra_agents: tuple[str, ...] = (),
) -> Any:
    monkeypatch.setenv("OPENMINION_HOME", str(root))
    monkeypatch.setenv("OPENMINION_DATA_ROOT", str(root / ".openminion"))
    monkeypatch.setenv("CAPTURE_AUTHZ_PROVIDER_KEY", "local-dummy-key")
    config = OpenMinionConfig()
    _csc_install_default_agent(config, provider="openai")
    for name in extra_agents:
        # A further agent on the same provider, with no identity profile.
        config.agents[name] = AgentProfileConfig(
            name=name, provider="openai", default_channel="console"
        )
    config.providers.openai.api_key_env = "CAPTURE_AUTHZ_PROVIDER_KEY"
    config.providers.openai.base_url = provider.base_url
    config.runtime.process_mode = "single-process"
    config.runtime.daemon_auto_start = False
    config.storage.path = str(root / ".openminion" / "state.db")
    config.security.tool_policy.internal_actor_scopes = list(internal_actor_scopes)
    if constraint is not None:
        _write_profile(root, constraint)
        config.identity.bundle_root = str(root / "identity")
        config.identity.db_path = str(root / ".openminion" / "identity.db")
    save_config(config, str(root / "agents.json"))
    manager = ConfigManager.load(
        str(root / "agents.json"), home_root=root, data_root=root / ".openminion"
    )
    return APIRuntime.from_manager(manager)


@dataclass
class _Recorder:
    """Call-through recorders on the agent's policy and memctl bundle write."""

    decisions: list[tuple[str, ...]] = field(default_factory=list)
    bundle_writes: list[dict[str, Any]] = field(default_factory=list)


def _record(runtime: Any, monkeypatch: pytest.MonkeyPatch) -> _Recorder:
    recorder = _Recorder()
    service = runtime.resolve_agent_service(AGENT_ID)
    policy = service._security_policy
    if policy is not None:
        evaluate = policy.evaluate

        def recording_evaluate(check: Any) -> Any:
            decision = evaluate(check)
            if check.action.tool_name == "memory.write":
                context = check.context
                recorder.decisions.append(
                    (
                        context.channel,
                        context.target,
                        context.session_id,
                        context.run_id,
                        decision.decision,
                        decision.reason_code,
                    )
                )
            return decision

        monkeypatch.setattr(policy, "evaluate", recording_evaluate)
    memctl = runtime.resolve_memory_assembly(AGENT_ID).memctl
    apply_bundle = memctl.apply_capture_bundle

    def recording_apply(**kwargs: Any) -> Any:
        recorder.bundle_writes.append(kwargs)
        return apply_bundle(**kwargs)

    monkeypatch.setattr(memctl, "apply_capture_bundle", recording_apply)
    return recorder


def _events(runtime: Any, event_type: str) -> list[Any]:
    sessions = runtime.sessions
    return [
        event
        for session in sessions.list_sessions(limit=100)
        for event in sessions.list_events(session_id=session.id, limit=1000)
        if event.event_type == event_type
    ]


def _intent(runtime: Any, session_id: str) -> dict[str, Any]:
    [event] = [
        event
        for event in _events(runtime, "turn.outcome")
        if event.session_id == session_id
    ]
    return dict(event.payload)


def _results(runtime: Any, capture_id: str) -> list[dict[str, Any]]:
    return [
        dict(event.payload)
        for event in _events(runtime, "memory.capture.result")
        if event.payload.get("capture_id") == capture_id
    ]


def _store_rows(runtime: Any) -> dict[str, int]:
    store = runtime.resolve_memory_assembly(AGENT_ID).service._store
    connection = sqlite3.connect(str(store._store.db_path))
    try:
        return {
            "capture_candidates": connection.execute(
                "SELECT COUNT(*) FROM memory_candidates"
                " WHERE json_extract(meta_json, '$.capture_id') IS NOT NULL"
            ).fetchone()[0],
            "receipts": connection.execute(
                "SELECT COUNT(*) FROM memory_capture_bundle_receipts"
            ).fetchone()[0],
            "tool_records": connection.execute(
                "SELECT COUNT(*) FROM memory_records WHERE key = ?",
                (TOOL_ARGS["key"],),
            ).fetchone()[0],
        }
    finally:
        connection.close()


def _turn(
    runtime: Any,
    provider: _Provider,
    session_id: str,
    **run_kwargs: Any,
) -> tuple[Any, list[str]]:
    mark = len(provider.requests)
    forced_tools = run_kwargs.pop("forced_tools", None)
    reply = run_kwargs.pop("reply", REPLY)
    with Agent(runtime=runtime, forced_tools=forced_tools) as agent:
        result = agent.run(MESSAGE, session_id=session_id, **run_kwargs)
    assert result.run_state == "completed"
    assert result.text == f"{AGENT_ID}: {reply}"
    return result, provider.requests[mark:]


def _bundle_result(result: Any) -> dict[str, Any]:
    return dict(json.loads(result.metadata["memory_capture_bundle_result"]))


def _recovery_allowed(runtime: Any, intent: dict[str, Any]) -> bool:
    identity = build_capture_identity(
        runtime_session_id=intent["runtime_session_id"],
        root_turn_id=intent["root_turn_id"],
    )
    service_port = AgentServiceTurnFlowAdapter(runtime.resolve_agent_service(AGENT_ID))
    return memory_capture_recovery_allowed(
        identity.runtime_session_id,
        identity.root_turn_id,
        identity.event_id,
        identity.capture_id,
        policy=service_port.security_policy,
        tools=service_port.tools,
        agent_id=service_port.identity_agent_id,
    )


def _assert_capture_kept_pending(
    runtime: Any,
    recorder: _Recorder,
    result: Any,
    requests: list[str],
    intent: dict[str, Any],
) -> None:
    assert "extraction" not in requests
    assert recorder.bundle_writes == []
    assert _results(runtime, intent["capture_id"]) == []
    assert intent["capture_state"] == "pending"
    assert result.metadata["memory_capture_state"] == "pending"
    assert _bundle_result(result) == {
        "capture_id": intent["capture_id"],
        "disposition": "pending",
        "error_code": UNAUTHORIZED,
    }


def _assert_capture_written(
    runtime: Any,
    recorder: _Recorder,
    result: Any,
    requests: list[str],
    intent: dict[str, Any],
) -> None:
    assert requests.count("extraction") == 1
    [write] = recorder.bundle_writes
    assert write["capture_id"] == intent["capture_id"]
    assert [item["normalized_key"] for item in write["candidates"]] == [
        CAPTURE_ITEM["normalized_key"]
    ]
    bundle = _bundle_result(result)
    assert bundle["disposition"] == "succeeded"
    assert len(bundle["output_ids"]) == 1
    assert result.metadata["memory_capture_state"] == "succeeded"
    [stored] = _results(runtime, intent["capture_id"])
    assert stored["output_ids"] == bundle["output_ids"]


@pytest.mark.parametrize("approval_callback", [False, True])
def test_identity_write_constraint_keeps_the_turn_capture_pending(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    provider: _Provider,
    approval_callback: bool,
) -> None:
    runtime = _runtime(tmp_path, monkeypatch, provider, constraint=WRITE_CONSTRAINT)
    approvals: list[tuple[Any, ...]] = []
    try:
        recorder = _record(runtime, monkeypatch)
        result, requests = _turn(
            runtime,
            provider,
            "capture",
            approval_callback=(
                (lambda *args: approvals.append(args) or True)
                if approval_callback
                else None
            ),
        )
        intent = _intent(runtime, "capture")
        _assert_capture_kept_pending(runtime, recorder, result, requests, intent)
        assert _store_rows(runtime) == {
            "capture_candidates": 0,
            "receipts": 0,
            "tool_records": 0,
        }
        assert approvals == []
        turn_decision = (
            "console",
            "memory-capture",
            "capture",
            intent["root_turn_id"],
            "require_approval",
            "identity_hard_constraint_restricted",
        )
        assert recorder.decisions == [turn_decision]

        # Recovery's gate decides the same for the same capture.
        assert _recovery_allowed(runtime, intent) is False
        assert recorder.decisions[1:] == [
            ("cron", "memory-capture-recovery", *turn_decision[2:])
        ]
        # The turn's tool adapter still asks approval for the memory.write tool.
        runner = runtime.resolve_agent_service(AGENT_ID)._get_runner()
        tool_decision = runner.tool_api.policy_adapter.evaluate(
            tool_name="memory.write",
            tool_spec=runtime.tools.get("memory.write"),
            args={},
        )
        assert (tool_decision.allowed, tool_decision.requires_confirm) == (False, True)
        assert tool_decision.reason == "identity_hard_constraint_restricted"
    finally:
        runtime.close()


@pytest.mark.parametrize("internal_actor_scopes", [(), ("tool.execute.elevated",)])
def test_denied_memory_write_keeps_the_turn_capture_pending(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    provider: _Provider,
    internal_actor_scopes: tuple[str, ...],
) -> None:
    runtime = _runtime(
        tmp_path,
        monkeypatch,
        provider,
        constraint=None,
        internal_actor_scopes=internal_actor_scopes,
    )
    try:
        # memory.write as a power-user tool: the policy denies an actor
        # without `tool.execute.elevated` (`missing_tool_scope`).
        spec = runtime.tools.get("memory.write")
        monkeypatch.setattr(spec, "min_scope", "POWER_USER")
        recorder = _record(runtime, monkeypatch)
        result, requests = _turn(runtime, provider, "capture")
        intent = _intent(runtime, "capture")
        _assert_capture_kept_pending(runtime, recorder, result, requests, intent)
        assert _store_rows(runtime)["capture_candidates"] == 0
        assert recorder.decisions == [
            (
                "console",
                "memory-capture",
                "capture",
                intent["root_turn_id"],
                "deny",
                "missing_tool_scope",
            )
        ]
        assert _recovery_allowed(runtime, intent) is False
        assert recorder.decisions[1][4:] == ("deny", "missing_tool_scope")

        # The configured internal actor scopes reach the turn's tool adapter
        # only: capture asks with recovery's actor, so both stay denied.
        runner = runtime.resolve_agent_service(AGENT_ID)._get_runner()
        tool_decision = runner.tool_api.policy_adapter.evaluate(
            tool_name="memory.write", tool_spec=spec, args={}
        )
        assert tool_decision.allowed is bool(internal_actor_scopes)
    finally:
        runtime.close()


@pytest.mark.parametrize(
    "case", ["neutral-constraint", "no-identity", "no-security-policy"]
)
def test_allowed_turn_capture_is_unchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    provider: _Provider,
    case: str,
) -> None:
    if case == "no-security-policy":
        monkeypatch.setenv("OPENMINION_DISABLE_SECURITY_POLICY", "true")
    constraint = NEUTRAL_CONSTRAINT if case == "neutral-constraint" else None
    runtime = _runtime(tmp_path, monkeypatch, provider, constraint=constraint)
    try:
        service = runtime.resolve_agent_service(AGENT_ID)
        assert (service._security_policy is None) is (case == "no-security-policy")
        recorder = _record(runtime, monkeypatch)
        result, requests = _turn(runtime, provider, "capture")
        intent = _intent(runtime, "capture")
        _assert_capture_written(runtime, recorder, result, requests, intent)
        assert _store_rows(runtime)["receipts"] == 1
        expected = (
            []
            if case == "no-security-policy"
            else [
                (
                    "console",
                    "memory-capture",
                    "capture",
                    intent["root_turn_id"],
                    "allow",
                    "allowed",
                )
            ]
        )
        assert recorder.decisions == expected
        assert _recovery_allowed(runtime, intent) is True
    finally:
        runtime.close()


@pytest.mark.parametrize("approve", [True, False])
def test_model_memory_write_approval_is_unchanged_and_not_borrowed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    provider: _Provider,
    approve: bool,
) -> None:
    runtime = _runtime(tmp_path, monkeypatch, provider, constraint=WRITE_CONSTRAINT)
    approvals: list[tuple[Any, ...]] = []
    provider.write_tool_call = True
    try:
        recorder = _record(runtime, monkeypatch)
        result, requests = _turn(
            runtime,
            provider,
            "capture",
            forced_tools=["memory.write"],
            approval_callback=lambda *args: approvals.append(args) or approve,
            reply=REPLY if approve else "Confirmation denied. The action was not run.",
        )
        intent = _intent(runtime, "capture")

        # The model's memory.write call goes through the existing approval:
        # one request, then, once approved, the tool policy's identity
        # restriction, which that approval satisfies.
        assert requests.count("tool_call") == 1
        assert [(name, args) for name, args, _id in approvals] == [
            ("memory.write", TOOL_ARGS)
        ]
        assert _store_rows(runtime)["tool_records"] == int(approve)
        assert [d[4:] for d in recorder.decisions if d[1] == "tool-runtime"] == (
            [("require_approval", "identity_hard_constraint_restricted")]
            if approve
            else []
        )

        # The turn's own capture does not use that approval.
        _assert_capture_kept_pending(runtime, recorder, result, requests, intent)
        assert [d for d in recorder.decisions if d[1] == "memory-capture"] == [
            (
                "console",
                "memory-capture",
                "capture",
                intent["root_turn_id"],
                "require_approval",
                "identity_hard_constraint_restricted",
            )
        ]
        assert _store_rows(runtime)["capture_candidates"] == 0
    finally:
        runtime.close()


@dataclass
class _Consolidation:
    counts: list[tuple[int, int, int]]
    extractor_root_turn_ids: list[str]


def _consolidate(
    runtime: Any, monkeypatch: pytest.MonkeyPatch, run_id: str
) -> _Consolidation:
    service = runtime.resolve_agent_service(AGENT_ID)
    assembly = runtime.resolve_memory_assembly(AGENT_ID)
    run = _Consolidation(counts=[], extractor_root_turn_ids=[])
    recover = assembly.recover_pending_captures
    extract = service.extract_memory_capture_candidates

    def recording_recover(**kwargs: Any) -> Any:
        outcome = recover(**kwargs)
        run.counts.append((outcome.scanned, outcome.recovered, outcome.pending))
        return outcome

    def recording_extract(*args: Any, **kwargs: Any) -> Any:
        run.extractor_root_turn_ids.append(kwargs["root_turn_id"])
        return extract(*args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(assembly, "recover_pending_captures", recording_recover)
        patch.setattr(service, "extract_memory_capture_candidates", recording_extract)
        executor = CronTurnExecutor(
            runtime=runtime,
            # The consolidation path does not use the cron store.
            cron_store=SimpleNamespace(),
            request_builder=lambda payload, agent_id, context=None, watch_context=None: (
                build_turn_request(
                    payload,
                    default_agent_id=agent_id,
                    trusted_memory_consolidation_context=context,
                    trusted_watch_context=watch_context,
                )
            ),
            timeout_s=60,
            task_manager=SimpleNamespace(
                get_task_by_job=lambda _job_id: SimpleNamespace(
                    metadata={"kind": "memory_consolidation"}
                )
            ),
        )
        executor.execute(
            {
                "job_id": "consolidation-job",
                "agent_id": AGENT_ID,
                "payload": {
                    "kind": "agentTurn",
                    "message": "Consolidate memory.",
                    CONSOLIDATION_PAYLOAD_KEY: {"target_scope": f"agent:{AGENT_ID}"},
                },
            },
            {"run_id": run_id, "due_at": "2026-10-28T00:00:00+00:00"},
        )
    return run


def test_recovery_commits_a_denied_turn_capture_once_the_write_is_allowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: _Provider
) -> None:
    runtime = _runtime(tmp_path, monkeypatch, provider, constraint=WRITE_CONSTRAINT)
    try:
        recorder = _record(runtime, monkeypatch)
        result, requests = _turn(runtime, provider, "capture")
        intent = _intent(runtime, "capture")
        _assert_capture_kept_pending(runtime, recorder, result, requests, intent)

        # Recovery asks its own unchanged gate: still denied, no extraction.
        mark = len(provider.requests)
        denied = _consolidate(runtime, monkeypatch, "consolidation-1")
        assert denied.counts == [(1, 0, 1)]
        assert denied.extractor_root_turn_ids == []
        assert "extraction" not in provider.requests[mark:]
        assert _results(runtime, intent["capture_id"]) == []
        restricted = ("require_approval", "identity_hard_constraint_restricted")
        # The consolidation turn asks its own memory.write question too (#496).
        assert [(d[0], d[1], *d[4:]) for d in recorder.decisions] == [
            ("console", "memory-capture", *restricted),
            ("cron", "memory-capture-recovery", *restricted),
            ("console", "memory-capture", *restricted),
        ]
    finally:
        runtime.close()

    # The same home, reopened with the neutral identity profile.
    _write_profile(tmp_path, NEUTRAL_CONSTRAINT)
    runtime = _runtime(tmp_path, monkeypatch, provider, constraint=NEUTRAL_CONSTRAINT)
    try:
        allowed = _consolidate(runtime, monkeypatch, "consolidation-2")
        again = _consolidate(runtime, monkeypatch, "consolidation-3")
        [stored] = _results(runtime, intent["capture_id"])
        rows = _store_rows(runtime)
    finally:
        runtime.close()

    assert allowed.counts == [(1, 1, 0)]
    assert allowed.extractor_root_turn_ids == [intent["root_turn_id"]]
    assert stored["disposition"] == "succeeded"
    assert len(stored["output_ids"]) == 1
    assert again.counts == [(0, 0, 0)]
    assert again.extractor_root_turn_ids == []
    assert rows["capture_candidates"] == 1
    assert rows["receipts"] == 1


def test_each_service_turn_sets_the_capture_decision_on_the_reused_runner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: _Provider
) -> None:
    runtime = _runtime(tmp_path, monkeypatch, provider, constraint=NEUTRAL_CONSTRAINT)
    try:
        service = runtime.resolve_agent_service(AGENT_ID)
        runner = service._get_runner()
        seen: list[tuple[Any, Any]] = []
        execute_turn = service._execute_turn

        def recording_execute_turn(**kwargs: Any) -> Any:
            seen.append(
                (
                    kwargs["runner"] is runner,
                    getattr(kwargs["runner"], "_capture_authorized_for_turn", None),
                )
            )
            return execute_turn(**kwargs)

        monkeypatch.setattr(service, "_execute_turn", recording_execute_turn)
        recorder = _record(runtime, monkeypatch)

        def constrain(constraint: str) -> None:
            _write_profile(tmp_path, constraint)
            service.identity_reload()

        def sdk_turn(session_id: str) -> tuple[str, int]:
            result, requests = _turn(runtime, provider, session_id)
            return (
                result.metadata["memory_capture_state"],
                requests.count("extraction"),
            )

        def mark() -> int:
            return len(provider.requests)

        outcomes = [sdk_turn("allowed-1")]

        # A scheduled consolidation turn: its capture is excluded.
        start = mark()
        consolidation = _consolidate(runtime, monkeypatch, "consolidation")
        outcomes.append(("excluded", provider.requests[start:].count("extraction")))
        outcomes.append(sdk_turn("allowed-2"))
        constrain(WRITE_CONSTRAINT)
        outcomes.append(sdk_turn("denied"))
        constrain(NEUTRAL_CONSTRAINT)
        outcomes.append(sdk_turn("allowed-3"))

        # A diagnostic turn, as `openminion agent-check` sends: terminal capture
        # is disabled.
        start = mark()
        asyncio.run(
            service.run_turn(
                Message(
                    channel="console",
                    target="api-user",
                    body=MESSAGE,
                    metadata={
                        "session_id": "runtime:agent-check:check-1",
                        "request_id": "check-1",
                        "turn_id": "check-1",
                        "invocation_scope": "runtime",
                        "diagnostic_scope": "runtime",
                    },
                )
            )
        )
        outcomes.append(("disabled", provider.requests[start:].count("extraction")))
        outcomes.append(sdk_turn("allowed-4"))
        capture_states = sorted(
            str(event.payload.get("capture_state"))
            for event in _events(runtime, "turn.outcome")
        )
    finally:
        runtime.close()

    assert consolidation.counts == [(0, 0, 0)]
    assert outcomes == [
        ("succeeded", 1),
        ("excluded", 0),
        ("succeeded", 1),
        ("pending", 0),
        ("succeeded", 1),
        ("disabled", 0),
        ("succeeded", 1),
    ]
    # One intent per runtime turn; the diagnostic turn commits none.
    assert capture_states == ["excluded"] + ["pending"] * 5
    assert seen == [
        (True, True),
        (True, False),
        (True, True),
        (True, False),
        (True, True),
        (True, False),
        (True, True),
    ]
    # Every turn asks once; excluded and disabled turns too, for their other
    # memory writes (#496), and their capture stays unauthorized.
    assert [d[4] for d in recorder.decisions if d[1] == "memory-capture"] == [
        "allow",
        "allow",
        "allow",
        "require_approval",
        "allow",
        "allow",
        "allow",
    ]
