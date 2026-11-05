"""Same-turn freshness retention through the SDK (#395).

`Agent(tools=[lookup_order], forced_tools=["lookup_order"])` runs "Check order
A-104" against a scripted OpenAI-compatible provider on 127.0.0.1. The
freshness classifier asks for an exact date, `lookup_order` returns no dated
field, and the judge closes with a final answer, so the closure freshness
guard turns that close into a continue and the turn takes a continuation step
with no user input. The entry requests offer only `lookup_order`, whose schema
has no `freshness` field, so the continuation entry carries no typed contract
unless the script adds one.

Read-only observers record the typed freshness state before and after each
`_apply_entry_freshness` call and what each closure freshness guard checked.
"""

from __future__ import annotations

import json
import sys
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterator

import pytest

from openminion import Agent, APIRuntime, tool
from openminion.base.config import OpenMinionConfig, save_config
from openminion.modules.brain.diagnostics.events import CanonicalEventLogger
from openminion.modules.brain.execution.runtime.closure import evaluator
from openminion.modules.brain.loop import orchestration
from tests.task_context_fixture import replace_entry_task

from tests._csc_fixtures import _csc_install_default_agent

CODE = "ZEBRA-42"
LIVE_NO_DATE = {
    "domain": "shopping",
    "time_sensitive": True,
    "needs_live_data": True,
    "needs_sources": True,
    "needs_exact_date": False,
    "answer_mode": "browse_then_answer",
}
FINALIZATION = (
    '<finalization_status>{"status":"final_answer","reasoning":"The order status '
    'and pickup code were retrieved with lookup_order."}</finalization_status>'
)
MISSING_DATE = "Missing exact-date evidence for an exact-date freshness answer."


def _completion(message: dict[str, Any], finish_reason: str) -> dict[str, Any]:
    return {
        "id": "chatcmpl-fixture",
        "object": "chat.completion",
        "created": 0,
        "model": "fixture-model",
        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
    }


def _tool_call(name: str, arguments: dict[str, Any], call_id: str) -> dict[str, Any]:
    call = {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }
    return _completion(
        {"role": "assistant", "content": None, "tool_calls": [call]}, "tool_calls"
    )


def _text(content: str) -> dict[str, Any]:
    return _completion({"role": "assistant", "content": content}, "stop")


def _all_text(body: dict[str, Any]) -> str:
    parts = []
    for message in body.get("messages") or []:
        content = message.get("content")
        if isinstance(content, list):
            content = " ".join(
                str(part.get("text", "")) for part in content if isinstance(part, dict)
            )
        parts.append(str(content or ""))
    return "\n".join(parts)


class Script:
    """Scripted provider; `continuation` is what continuation entries answer.

    - "text": a text reply
    - "tool_then_text": `lookup_order` again without a contract, then text
    - "contract_then_text": `lookup_order` with the LIVE_NO_DATE contract, then text
    """

    def __init__(self, continuation: str) -> None:
        self.continuation = continuation
        self.continuation_entries = 0
        self.kinds: list[str] = []
        self.lock = threading.Lock()
        self.order = "A-104"

    def respond(self, body: dict[str, Any]) -> dict[str, Any]:
        text = _all_text(body)
        choice = body.get("tool_choice")
        forced = (
            str((choice.get("function") or {}).get("name", ""))
            if isinstance(choice, dict)
            else ""
        )
        messages = body.get("messages") or []
        after_tool = any(message.get("role") == "tool" for message in messages)
        users = [
            str(m.get("content") or "") for m in messages if m.get("role") == "user"
        ]
        request = users[-1] if users else ""
        if request.startswith("Check order"):
            self.order = "B-205" if "B-205" in request else "A-104"
        order = self.order
        with self.lock:
            index = len(self.kinds)
            if forced == "submit_output":
                if "purpose: judge" in text or "[JUDGE CONTEXT]" in text:
                    kind = "judge"
                elif "purpose: reflect" in text:
                    kind = "reflect"
                elif "freshness-routing classifier" in text:
                    kind = "freshness"
                else:
                    kind = "other_structured"
            elif "purpose: decide" in text:
                kind = "entry_after_tool" if after_tool else "entry"
            else:
                kind = "act_loop" if after_tool else "other_text"
            self.kinds.append(kind)
            if kind == "entry_after_tool":
                self.continuation_entries += 1
            continuation_index = self.continuation_entries
        if kind == "reflect":
            return _tool_call(
                "submit_output",
                {
                    "items": [
                        {
                            "title": f"Check order {order}",
                            "normalized_key": f"task:check_order_{order}",
                            "kind": "task",
                            "content": f"Check order {order}",
                        }
                    ]
                },
                f"call-reflect-{index}",
            )
        if kind == "freshness":
            return _tool_call(
                "submit_output",
                {
                    "intent": "order_status_inquiry",
                    "domain": "shopping",
                    "time_sensitive": True,
                    "needs_live_data": True,
                    "needs_sources": True,
                    "needs_exact_date": "B-205" not in text,
                    "answer_mode": "browse_then_answer",
                    "reason": "Order status is live data.",
                    "confidence": 1,
                },
                f"call-freshness-{index}",
            )
        if kind == "judge":
            return _tool_call(
                "submit_output",
                {
                    "final_answer": f"[judge] Order {order} is ready, code {CODE}.",
                    "satisfied": True,
                    "next_action": "close",
                },
                f"call-judge-{index}",
            )
        if kind == "entry":
            return _tool_call(
                "lookup_order", {"order_id": order}, f"call-lookup-{index}"
            )
        if kind == "entry_after_tool":
            if continuation_index == 1 and self.continuation == "tool_then_text":
                return _tool_call(
                    "lookup_order", {"order_id": order}, f"call-lookup-{index}"
                )
            if continuation_index == 1 and self.continuation == "contract_then_text":
                return _tool_call(
                    "lookup_order",
                    {"order_id": order, "freshness": dict(LIVE_NO_DATE)},
                    f"call-lookup-{index}",
                )
            return _text(f"[continuation] Order {order} is ready, code {CODE}.")
        if kind == "act_loop":
            return _text(
                f"[loop] Order {order} is ready, code {CODE}.\n\n{FINALIZATION}"
            )
        if kind == "other_structured":
            return _tool_call("submit_output", {}, f"call-other-{index}")
        return _text("ok")


@pytest.fixture
def observed(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[dict[str, Any]]]:
    """Record entry freshness steps, closure freshness guards and closure gates."""
    records: dict[str, list[dict[str, Any]]] = {"entry": [], "guard": [], "gate": []}

    def typed(state: Any) -> dict[str, Any] | None:
        if state.freshness_contract is None:
            return None
        return {
            "needs_exact_date": state.freshness_contract.needs_exact_date,
            "time_sensitive": state.freshness_contract.time_sensitive,
            "require_exact_date": state.freshness_obligations.require_exact_date,
            "classifier_mode": state.freshness_diagnostics.classifier_mode,
        }

    real_entry = orchestration._apply_entry_freshness

    def entry(**kwargs: Any) -> Any:
        before = typed(kwargs["state"])
        result = real_entry(**kwargs)
        records["entry"].append(
            {
                "user_input": kwargs.get("user_input"),
                "before": before,
                "after": typed(kwargs["state"]),
            }
        )
        return result

    real_verify = evaluator.verify_freshness_answer

    def verify(**kwargs: Any) -> list[str]:
        reasons = real_verify(**kwargs)
        if sys._getframe(1).f_code.co_name == "_apply_freshness_guard":
            records["guard"].append(
                {
                    "require_exact_date": kwargs["obligations"].require_exact_date,
                    "result": list(reasons),
                }
            )
        return reasons

    real_emit = CanonicalEventLogger.emit

    def emit(self: Any, event_type: str, payload: dict[str, Any], *a: Any, **k: Any):
        if event_type == "brain.closure_gate.completed":
            records["gate"].append(
                {
                    "next_action": payload.get("next_action"),
                    "reason": payload.get("reason"),
                }
            )
        return real_emit(self, event_type, payload, *a, **k)

    monkeypatch.setattr(orchestration, "_apply_entry_freshness", entry)
    monkeypatch.setattr(evaluator, "verify_freshness_answer", verify)
    monkeypatch.setattr(CanonicalEventLogger, "emit", emit)
    return records


@contextmanager
def _scripted_agent(
    script: Script, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[Agent, list[str]]]:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            response = script.respond(body)
            response["choices"][0]["message"] = replace_entry_task(
                body, response["choices"][0]["message"]
            )
            payload = json.dumps(response).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_args: Any) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    monkeypatch.setenv("OPENMINION_DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setenv("TEST_PROVIDER_KEY", "local-dummy-key")
    config = OpenMinionConfig()
    _csc_install_default_agent(config, provider="openai")
    config.providers.openai.api_key_env = "TEST_PROVIDER_KEY"
    config.providers.openai.base_url = f"http://127.0.0.1:{server.server_port}/v1"
    config.runtime.process_mode = "single-process"
    config.runtime.daemon_auto_start = False
    config.storage.path = str(tmp_path / "state.db")
    save_config(config, str(tmp_path / "agents.json"))
    calls: list[str] = []

    @tool(min_scope="READ_ONLY")
    def lookup_order(order_id: str) -> str:
        """Return the current status and pickup code for one order."""
        calls.append(order_id)
        return f"order {order_id}: ready for pickup, pickup code {CODE}"

    runtime = APIRuntime.from_config_path(
        str(tmp_path / "agents.json"), home_root=tmp_path, data_root=tmp_path / "data"
    )
    try:
        with Agent(
            runtime=runtime,
            instructions="Use lookup_order, then answer in one sentence with the code.",
            tools=[lookup_order],
            forced_tools=["lookup_order"],
        ) as agent:
            yield agent, calls
    finally:
        try:
            runtime.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


EXACT = {
    "needs_exact_date": True,
    "time_sensitive": True,
    "require_exact_date": True,
    "classifier_mode": "llm",
}


def _first_step_is_classified_and_blocked(observed: dict[str, list]) -> None:
    first = observed["entry"][0]
    assert (first["user_input"], first["before"], first["after"]) == (
        "Check order A-104",
        None,
        EXACT,
    )
    assert observed["guard"][0] == {
        "require_exact_date": True,
        "result": [MISSING_DATE],
    }
    assert observed["gate"][0] == {
        "next_action": "continue",
        "reason": "freshness_verifier_blocked",
    }


def test_text_continuation_keeps_the_exact_date_obligation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, observed: dict[str, list]
) -> None:
    script = Script("text")
    with _scripted_agent(script, tmp_path, monkeypatch) as (agent, _calls):
        agent.run("Check order A-104", session_id="fresh-b", timeout_seconds=120)

    _first_step_is_classified_and_blocked(observed)
    continuation = observed["entry"][1]
    assert continuation == {"user_input": None, "before": EXACT, "after": EXACT}
    assert script.kinds.count("freshness") == 1


def test_tool_continuation_keeps_the_obligation_and_the_guard_blocks_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, observed: dict[str, list]
) -> None:
    script = Script("tool_then_text")
    with _scripted_agent(script, tmp_path, monkeypatch) as (agent, calls):
        result = agent.run(
            "Check order A-104", session_id="fresh-e", timeout_seconds=120
        )

    _first_step_is_classified_and_blocked(observed)
    assert calls == ["A-104", "A-104"]
    assert observed["entry"][1] == {"user_input": None, "before": EXACT, "after": EXACT}
    # The second close, on the same undated lookup_order output, is blocked too.
    assert observed["guard"][1] == {
        "require_exact_date": True,
        "result": [MISSING_DATE],
    }
    assert observed["gate"][1] == {
        "next_action": "continue",
        "reason": "freshness_verifier_blocked",
    }
    assert "[judge]" not in result.text
    assert script.kinds.count("freshness") == 1


def test_valid_contract_on_a_continuation_replaces_the_obligation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, observed: dict[str, list]
) -> None:
    script = Script("contract_then_text")
    with _scripted_agent(script, tmp_path, monkeypatch) as (agent, calls):
        result = agent.run(
            "Check order A-104", session_id="fresh-a", timeout_seconds=120
        )

    _first_step_is_classified_and_blocked(observed)
    assert observed["entry"][1] == {
        "user_input": None,
        "before": EXACT,
        "after": {
            "needs_exact_date": False,
            "time_sensitive": True,
            "require_exact_date": False,
            "classifier_mode": "entry_contract",
        },
    }
    # The replaced contract needs no exact date, so the second close passes.
    assert observed["guard"][1] == {"require_exact_date": False, "result": []}
    assert calls == ["A-104", "A-104"]
    assert result.text.endswith(f"[judge] Order A-104 is ready, code {CODE}.")


def test_new_user_input_on_the_next_turn_is_classified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, observed: dict[str, list]
) -> None:
    script = Script("text")
    with _scripted_agent(script, tmp_path, monkeypatch) as (agent, calls):
        agent.run("Check order A-104", session_id="fresh-n", timeout_seconds=120)
        second = agent.run(
            "Check order B-205", session_id="fresh-n", timeout_seconds=120
        )

    turn_two = [
        step for step in observed["entry"] if step["user_input"] == "Check order B-205"
    ]
    assert turn_two == [
        {
            "user_input": "Check order B-205",
            "before": None,
            "after": {
                "needs_exact_date": False,
                "time_sensitive": True,
                "require_exact_date": False,
                "classifier_mode": "llm",
            },
        }
    ]
    assert script.kinds.count("freshness") == 2
    assert calls == ["A-104", "B-205"]
    assert second.text.endswith(f"[judge] Order B-205 is ready, code {CODE}.")
