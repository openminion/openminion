"""Text entry answers go through the freshness finalization, `final_close_message`.

A scripted OpenAI-compatible provider on 127.0.0.1 drives
`Agent(tools=[lookup_order])`, and `lookup_order` returns no dated field. The
observers of `verify_freshness_answer` and of each entry step's obligations are
read-only.
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
from openminion.modules.brain.execution.closure import base as closure_base
from openminion.modules.brain.loop import orchestration
from openminion.modules.brain.runtime.verification.policy import (
    build_freshness_failure_message,
)
from openminion.modules.brain.schemas.freshness import FreshnessContract
from tests._csc_fixtures import _csc_install_default_agent

CODE = "ZEBRA-42"
ENTRY_TEXT = f"[entry text] Order A-104 is ready, code {CODE}."
CONTINUATION_TEXT = f"[continuation text] Order A-104 is ready, code {CODE}."
JUDGE_TEXT = f"[judge] Order A-104 is ready, code {CODE}."
FINALIZATION = (
    '<finalization_status>{"status":"final_answer","reasoning":"The order status '
    'and pickup code were retrieved with lookup_order."}</finalization_status>'
)
MISSING_LIVE = "Missing live-data evidence for a freshness-sensitive answer."
MISSING_DATE = "Missing exact-date evidence for an exact-date freshness answer."
LIVE_EXACT = {
    "time_sensitive": True,
    "needs_live_data": True,
    "needs_sources": True,
    "needs_exact_date": True,
    "answer_mode": "browse_then_answer",
}
LIVE_NO_DATE = {**LIVE_EXACT, "needs_exact_date": False}
SOURCES_ONLY = {
    "time_sensitive": True,
    "needs_live_data": False,
    "needs_sources": True,
    "needs_exact_date": False,
    "answer_mode": "local_only",
}
NOT_TIME_SENSITIVE = {
    "time_sensitive": False,
    "needs_live_data": False,
    "needs_sources": False,
    "needs_exact_date": False,
    "answer_mode": "local_only",
}
SHOPPING = FreshnessContract(domain="shopping", time_sensitive=True)


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
    """Scripted provider.

    `classifier` is the freshness classifier's answer. `first_entry` is what
    the first entry step answers: "text", or "tool" (`lookup_order`, after
    which the judge closes and continuation entries answer in text).
    """

    def __init__(self, *, classifier: dict[str, Any], first_entry: str) -> None:
        self.classifier = classifier
        self.first_entry = first_entry
        self.lock = threading.Lock()
        self.kinds: list[str] = []

    def respond(self, body: dict[str, Any]) -> dict[str, Any]:
        text = _all_text(body)
        choice = body.get("tool_choice")
        forced = (
            str((choice.get("function") or {}).get("name", ""))
            if isinstance(choice, dict)
            else ""
        )
        after_tool = any(m.get("role") == "tool" for m in body.get("messages") or [])
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
        with self.lock:
            index = len(self.kinds)
            self.kinds.append(kind)
        if kind == "reflect":
            item = {
                "title": "Check order A-104",
                "normalized_key": "task:check_order_a104",
                "kind": "task",
                "content": "Check order A-104",
            }
            return _tool_call("submit_output", {"items": [item]}, f"call-r-{index}")
        if kind == "freshness":
            answer = {
                "intent": "order_status_inquiry",
                "domain": "shopping",
                **self.classifier,
                "reason": "Order status questions.",
                "confidence": 1,
            }
            return _tool_call("submit_output", answer, f"call-f-{index}")
        if kind == "judge":
            verdict = {"final_answer": JUDGE_TEXT, "satisfied": True}
            verdict["next_action"] = "close"
            return _tool_call("submit_output", verdict, f"call-j-{index}")
        if kind == "entry":
            if self.first_entry == "text":
                return _text(ENTRY_TEXT)
            return _tool_call("lookup_order", {"order_id": "A-104"}, f"call-l-{index}")
        if kind == "entry_after_tool":
            return _text(CONTINUATION_TEXT)
        if kind == "act_loop":
            return _text(f"[loop] Order A-104 is ready, code {CODE}.\n\n{FINALIZATION}")
        if kind == "other_structured":
            return _tool_call("submit_output", {}, f"call-o-{index}")
        return _text("ok")


def _obligations(obligations: Any) -> list[bool] | None:
    if obligations is None:
        return None
    return [
        obligations.require_live_data,
        obligations.require_sources,
        obligations.require_exact_date,
    ]


@pytest.fixture
def observed(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[dict[str, Any]]]:
    """Record respond-route freshness checks and each entry step's obligations."""
    records: dict[str, list[dict[str, Any]]] = {"respond": [], "entry": []}
    real_verify = closure_base.verify_freshness_answer

    def verify(**kwargs: Any) -> list[str]:
        reasons = real_verify(**kwargs)
        caller = sys._getframe(1)
        if (
            caller.f_code.co_name == "final_close_message"
            and caller.f_back is not None
            and caller.f_back.f_code.co_name == "_respond_execute"
        ):
            contract = kwargs["contract"]
            records["respond"].append(
                {
                    "answer": kwargs["answer"],
                    "time_sensitive": getattr(contract, "time_sensitive", None),
                    "obligations": _obligations(kwargs["obligations"]),
                    "result": list(reasons),
                }
            )
        return reasons

    real_entry = orchestration._apply_entry_freshness

    def entry(**kwargs: Any) -> Any:
        result = real_entry(**kwargs)
        state = kwargs["state"]
        records["entry"].append(
            {
                "user_input": kwargs.get("user_input"),
                "obligations": _obligations(state.freshness_obligations),
            }
        )
        return result

    monkeypatch.setattr(closure_base, "verify_freshness_answer", verify)
    monkeypatch.setattr(orchestration, "_apply_entry_freshness", entry)
    return records


@contextmanager
def _scripted_agent(
    script: Script,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    forced_tools: list[str],
) -> Iterator[tuple[Agent, list[str]]]:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            payload = json.dumps(script.respond(body)).encode()
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
            forced_tools=forced_tools,
        ) as agent:
            yield agent, calls
    finally:
        try:
            runtime.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


def _run(
    script: Script,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    forced_tools: list[str],
) -> tuple[str, list[str]]:
    with _scripted_agent(script, tmp_path, monkeypatch, forced_tools=forced_tools) as (
        agent,
        calls,
    ):
        result = agent.run("Check order A-104", session_id="text", timeout_seconds=120)
    return result.text, calls


def test_first_entry_text_without_the_required_evidence_is_a_freshness_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, observed: dict[str, list]
) -> None:
    script = Script(classifier=LIVE_EXACT, first_entry="text")

    text, calls = _run(script, tmp_path, monkeypatch, forced_tools=[])

    assert calls == []
    # The existing verifier checks the entry's own text against the turn's
    # obligations; the text has no evidence behind it.
    assert observed["respond"] == [
        {
            "answer": ENTRY_TEXT,
            "time_sensitive": True,
            "obligations": [True, True, True],
            "result": [MISSING_LIVE, MISSING_DATE],
        }
    ]
    expected = build_freshness_failure_message(
        contract=SHOPPING, reasons=[MISSING_LIVE, MISSING_DATE]
    )
    assert text.endswith(expected)
    assert ENTRY_TEXT not in text


def test_continuation_text_without_the_required_evidence_is_a_freshness_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, observed: dict[str, list]
) -> None:
    """Same-turn continuation after the closure guard blocks the judge's close.

    The continuation entry step keeps the turn's obligations (#422) and
    answers in text. Its evidence is the turn's `lookup_order` result, which
    has no dated field, so the existing verifier reports the missing exact
    date and the turn ends with the freshness failure, not with the
    continuation's text, the judge's blocked answer or the tool loop's summary.
    """
    script = Script(classifier=LIVE_EXACT, first_entry="tool")

    text, calls = _run(script, tmp_path, monkeypatch, forced_tools=["lookup_order"])

    assert calls == ["A-104"]
    assert script.kinds.count("judge") == 1
    assert observed["entry"] == [
        {"user_input": "Check order A-104", "obligations": [True, True, True]},
        {"user_input": None, "obligations": [True, True, True]},
    ]
    assert observed["respond"] == [
        {
            "answer": CONTINUATION_TEXT,
            "time_sensitive": True,
            "obligations": [True, True, True],
            "result": [MISSING_DATE],
        }
    ]
    expected = build_freshness_failure_message(
        contract=SHOPPING, reasons=[MISSING_DATE]
    )
    assert text.endswith(expected)
    assert CONTINUATION_TEXT not in text
    assert JUDGE_TEXT not in text
    assert "[loop]" not in text


def test_first_entry_text_meeting_its_obligations_is_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, observed: dict[str, list]
) -> None:
    script = Script(classifier=SOURCES_ONLY, first_entry="text")

    text, _calls = _run(script, tmp_path, monkeypatch, forced_tools=[])

    assert observed["respond"] == [
        {
            "answer": ENTRY_TEXT,
            "time_sensitive": True,
            "obligations": [False, True, False],
            "result": [],
        }
    ]
    assert text.endswith(ENTRY_TEXT)


def test_first_entry_text_without_freshness_obligations_is_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, observed: dict[str, list]
) -> None:
    script = Script(classifier=NOT_TIME_SENSITIVE, first_entry="text")

    text, _calls = _run(script, tmp_path, monkeypatch, forced_tools=[])

    assert observed["respond"] == [
        {
            "answer": ENTRY_TEXT,
            "time_sensitive": False,
            "obligations": [False, False, False],
            "result": [],
        }
    ]
    assert text.endswith(ENTRY_TEXT)


def test_judge_close_on_the_tool_path_is_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, observed: dict[str, list]
) -> None:
    script = Script(classifier=LIVE_NO_DATE, first_entry="tool")

    text, calls = _run(script, tmp_path, monkeypatch, forced_tools=["lookup_order"])

    # The closure guard accepts the lookup_order output for a live answer
    # without an exact date, so the judge's close ends the turn; the respond
    # route is not taken.
    assert calls == ["A-104"]
    assert observed["respond"] == []
    assert text.endswith(JUDGE_TEXT)


SECOND_TURN_TEXT = f"[second turn] Order A-104 is ready, code {CODE}."


class ContinueScript(Script):
    """The judge asks to continue; continuation entries answer in text.

    From `second_turn` on, every entry step answers `SECOND_TURN_TEXT`.
    """

    second_turn = False

    def respond(self, body: dict[str, Any]) -> dict[str, Any]:
        result = super().respond(body)
        kind = self.kinds[-1]
        if kind == "judge":
            verdict = {
                "final_answer": JUDGE_TEXT,
                "satisfied": False,
                "next_action": "continue",
                "reason": "Summarize the observed order status in a final answer.",
            }
            return _tool_call("submit_output", verdict, "continue-judge")
        if self.second_turn and kind in {"entry", "entry_after_tool"}:
            return _text(SECOND_TURN_TEXT)
        return result


def test_continuation_text_with_the_turns_live_evidence_is_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, observed: dict[str, list]
) -> None:
    """The judge continues after a successful lookup; the continuation answers.

    The lookup meets the live-data obligation as it does when the judge
    closes directly, so the continuation's text is kept, not replaced by the
    judge's answer or the tool loop's summary.
    """
    script = ContinueScript(classifier=LIVE_NO_DATE, first_entry="tool")

    text, calls = _run(script, tmp_path, monkeypatch, forced_tools=["lookup_order"])

    assert calls == ["A-104"]
    assert script.kinds.count("judge") == 1
    assert observed["respond"] == [
        {
            "answer": CONTINUATION_TEXT,
            "time_sensitive": True,
            "obligations": [True, True, False],
            "result": [],
        }
    ]
    assert text.endswith(CONTINUATION_TEXT)
    assert JUDGE_TEXT not in text
    assert "[loop]" not in text


def test_evidence_from_an_earlier_turn_does_not_satisfy_the_next_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, observed: dict[str, list]
) -> None:
    """Turn 1 hands its lookup to a continuation; turn 2 answers in text.

    Both turns run in one conversation, as the HTTP turns route and the
    interactive CLI send them, so turn 2 starts from turn 1's working state.
    """
    real_build_payload = Agent._build_payload

    def build_payload(self: Agent, message: str, **kwargs: Any) -> dict[str, Any]:
        payload = real_build_payload(self, message, **kwargs)
        payload["conversation_id"] = "order-chat"
        return payload

    monkeypatch.setattr(Agent, "_build_payload", build_payload)
    script = ContinueScript(classifier=LIVE_NO_DATE, first_entry="tool")

    with _scripted_agent(
        script, tmp_path, monkeypatch, forced_tools=["lookup_order"]
    ) as (agent, calls):
        first = agent.run("Check order A-104", session_id="text", timeout_seconds=120)
        script.second_turn = True
        second = agent.run(
            "Is order A-104 still ready?", session_id="text", timeout_seconds=120
        )

    assert calls == ["A-104"]
    assert first.text.endswith(CONTINUATION_TEXT)
    assert observed["respond"] == [
        {
            "answer": CONTINUATION_TEXT,
            "time_sensitive": True,
            "obligations": [True, True, False],
            "result": [],
        },
        {
            "answer": SECOND_TURN_TEXT,
            "time_sensitive": True,
            "obligations": [True, True, False],
            "result": [MISSING_LIVE],
        },
    ]
    expected = build_freshness_failure_message(
        contract=SHOPPING, reasons=[MISSING_LIVE]
    )
    assert second.text.endswith(expected)
    assert SECOND_TURN_TEXT not in second.text
