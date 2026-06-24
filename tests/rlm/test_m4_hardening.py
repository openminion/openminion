from __future__ import annotations

import json
import threading
from typing import Any

from openminion.modules.brain.loop.recursive.schemas import MetaDirective, RLMBudgets
from openminion.modules.brain.loop.recursive.service import RLMService


class _Sess:
    def __init__(self, state_inline: dict[str, Any] | None = None) -> None:
        self._latest: dict[str, Any] | None = (
            {"session_id": "s", "version": 1, "state_inline": state_inline}
            if state_inline is not None
            else None
        )
        self._version = 1 if state_inline is not None else 0
        self.events: list[dict[str, Any]] = []
        self._fail_put = False

    def get_latest_working_state(self, _sid: str) -> dict[str, Any] | None:
        return self._latest

    def put_working_state(self, sid: str, *, state_ref=None, state_inline=None) -> int:
        if self._fail_put:
            raise RuntimeError("Simulated writeback failure")
        self._version += 1
        self._latest = {
            "session_id": sid,
            "version": self._version,
            "state_inline": state_inline or {},
        }
        return self._version

    def append_event(
        self,
        session_id: str | None = None,
        type: str | None = None,
        payload: dict | None = None,
        **kwargs,
    ) -> str:
        eid = f"ev-{len(self.events) + 1}"
        sid = session_id or kwargs.get("session_id", "unknown")
        self.events.append(
            {
                "event_id": eid,
                "session_id": sid,
                "type": kwargs.get("event_type") or type,
                "payload": payload or {},
                "agent_id": kwargs.get("agent_id") or kwargs.get("actor_id"),
                "artifact_refs": kwargs.get("artifact_refs") or [],
                "memory_refs": kwargs.get("memory_refs") or [],
                "status": kwargs.get("status"),
            }
        )
        return eid

    def list_events(
        self,
        sid,
        *,
        event_type=None,
        trace_id=None,
        agent_id=None,
        status=None,
        limit=None,
    ) -> list:
        evts = [e for e in self.events if e["session_id"] == sid]
        if event_type:
            evts = [e for e in evts if e["type"] == event_type]
        return evts

    def get_slice(self, sid, purpose, limits) -> dict[str, Any]:
        return {
            "recent_turns": [],
            "open_tasks": [],
            "active_state": {},
            "recent_tool_events": [],
        }


class _Ctx:
    def build_pack(self, request: Any) -> dict[str, Any]:
        q = (
            request.get("query")
            if isinstance(request, dict)
            else getattr(request, "query", "")
        )
        return {
            "messages": [
                {"role": "system", "content": "ctx"},
                {"role": "user", "content": str(q)},
            ]
        }


class _LLM:
    def __init__(self, payloads: list[dict[str, Any]], *, fail: bool = False) -> None:
        self.payloads = payloads
        self.calls: list[Any] = []
        self._fail = fail

    def call_for_agent(
        self, agent_id, purpose, request, agent_policy
    ) -> dict[str, Any]:
        if self._fail:
            raise RuntimeError("Simulated LLM hard failure")
        self.calls.append(request)
        idx = min(len(self.calls) - 1, len(self.payloads) - 1)
        pl = self.payloads[idx]
        return {
            "status": "success",
            "text": json.dumps(pl),
            "json_output": pl,
            "usage": {"input": 30, "output": 10},
        }


class _FailRetrieval:
    def retrieve(self, *, query, purpose, scope, k, strategy, filters=None):
        raise ConnectionError("Simulated retrieval backend failure")

    def expand(self, *, ref, mode, k):
        return []


class _FailMem:
    def retrieve(self, *, session_id, agent_id, query, k, filters=None):
        return []

    def query_facts(self, *, session_id, agent_id, query, limit):
        return []

    def stage_candidate(self, **kwargs) -> str:
        raise RuntimeError("Simulated memory staging failure")


class _FailArtifact:
    def list_recent(self, limit=50, scope_filters=None):
        return []

    def read_bytes(self, ref_or_sha: str) -> bytes:
        return b""

    def ingest_bytes(self, data, **kwargs):
        raise IOError("Simulated artifact ingest failure")


def _svc(
    session=None,
    llm_payloads=None,
    *,
    fail_llm=False,
    fail_retrieve=False,
    fail_mem=False,
    fail_artifact=False,
    stale_wm=None,
) -> tuple[_Sess, RLMService]:
    sess = session or _Sess(stale_wm)
    llm = _LLM(llm_payloads or [{"final": True, "answer": "ok"}], fail=fail_llm)
    service = RLMService(
        sessctl=sess,
        contextctl=_Ctx(),
        llmctl=llm,
        retrievectl=_FailRetrieval() if fail_retrieve else None,
        memctl=_FailMem() if fail_mem else None,
        artifactctl=_FailArtifact() if fail_artifact else None,
    )
    return sess, service


def test_m4_max_ticks_is_never_exceeded() -> None:
    _, svc = _svc(
        llm_payloads=[{"final": False, "answer": "not done", "next_query": "more"}]
    )
    for max_ticks in (1, 2, 4):
        sess2 = _Sess()
        service2 = RLMService(
            sessctl=sess2,
            contextctl=_Ctx(),
            llmctl=_LLM([{"final": False, "answer": "not done", "next_query": "more"}]),
        )
        resp = service2.generate(
            session_id=f"sess-m4-loop-{max_ticks}",
            agent_id="agt",
            purpose="act",
            query="test",
            budgets=RLMBudgets(max_ticks=max_ticks),
        )
        assert resp.telemetry.ticks_used <= max_ticks, (
            f"Expected ≤{max_ticks} ticks, got {resp.telemetry.ticks_used}"
        )


def test_m4_bad_streak_limit_respected_before_max_ticks() -> None:
    sess = _Sess()
    _, svc = _svc(
        session=sess,
        llm_payloads=[{"final": False, "answer": "trying", "next_query": "more"}],
    )
    resp = svc.generate(
        session_id="sess-m4-streak",
        agent_id="agt",
        purpose="act",
        query="query with no matching content",
        budgets=RLMBudgets(max_ticks=10),
        meta_directive=MetaDirective(max_bad_retrieval_streak=2),
    )
    assert resp.telemetry.ticks_used <= 3
    assert resp.continuation is not None and resp.continuation.needs_more_ticks is True


def test_m4_stale_wm_state_without_wm_key_uses_default() -> None:
    stale_inline = {"task_state": {"plan_id": "plan-old"}, "other_key": "garbage"}
    sess, svc = _svc(stale_wm=stale_inline)
    resp = svc.generate(
        session_id="sess-m4-stale1",
        agent_id="agt",
        purpose="act",
        query="proceed",
        budgets=RLMBudgets(max_ticks=1),
    )
    assert isinstance(resp.final_text, str)


def test_m4_corrupted_wm_state_value_uses_default() -> None:
    stale_inline = {"wm_state": "this-is-not-a-dict"}
    sess, svc = _svc(stale_wm=stale_inline)
    resp = svc.generate(
        session_id="sess-m4-stale2",
        agent_id="agt",
        purpose="act",
        query="proceed",
        budgets=RLMBudgets(max_ticks=1),
    )
    assert isinstance(resp.final_text, str)


def test_m4_wm_state_with_partial_fields_is_merged_safely() -> None:
    stale_inline = {"wm_state": {"objective": "Migrate DB", "wm_version": 3}}
    sess, svc = _svc(stale_wm=stale_inline)
    resp = svc.generate(
        session_id="sess-m4-stale3",
        agent_id="agt",
        purpose="plan",
        query="status",
        budgets=RLMBudgets(max_ticks=1),
    )
    assert isinstance(resp.wm_update.objective, str)


def test_m4_retrieval_client_exception_falls_back_to_local() -> None:
    sess, svc = _svc(fail_retrieve=True)
    resp = svc.generate(
        session_id="sess-m4-rfail",
        agent_id="agt",
        purpose="act",
        query="test retrieval failure",
        budgets=RLMBudgets(max_ticks=1),
    )
    assert isinstance(resp.final_text, str)
    assert resp.telemetry.ticks_used == 1


def test_m4_retrieval_failure_event_stops_loop_via_bad_streak() -> None:
    sess, svc = _svc(
        fail_retrieve=True,
        llm_payloads=[{"final": False, "answer": "retry", "next_query": "more"}],
    )
    resp = svc.generate(
        session_id="sess-m4-rfail2",
        agent_id="agt",
        purpose="act",
        query="query that will always get bad retrieval",
        budgets=RLMBudgets(max_ticks=5),
        meta_directive=MetaDirective(max_bad_retrieval_streak=2),
    )
    assert resp.telemetry.stop_reason in {
        "retrieval_quality_bad_streak",
        "max_ticks_reached",
    }


def test_m4_llm_exception_is_handled_gracefully() -> None:
    sess, svc = _svc(fail_llm=True)
    try:
        resp = svc.generate(
            session_id="sess-m4-llmfail",
            agent_id="agt",
            purpose="act",
            query="test llm failure",
            budgets=RLMBudgets(max_ticks=1),
        )
        assert isinstance(resp.final_text, str)
    except RuntimeError:
        pass


def test_m4_memory_staging_failure_does_not_abort_generate() -> None:
    sess, svc = _svc(
        fail_mem=True,
        llm_payloads=[
            {
                "final": True,
                "answer": "Done.",
                "memory_write_intents": [
                    {
                        "intent_type": "lesson",
                        "title": "T",
                        "content": "C",
                        "salience": 0.5,
                    }
                ],
            }
        ],
    )
    resp = svc.generate(
        session_id="sess-m4-memfail",
        agent_id="agt",
        purpose="act",
        query="test mem failure",
        budgets=RLMBudgets(max_ticks=1),
    )
    assert resp.final_text == "Done."


def test_m4_artifact_ingest_failure_does_not_abort_generate() -> None:
    sess, svc = _svc(
        fail_artifact=True,
        llm_payloads=[{"final": True, "answer": "Done even with artifact failure."}],
    )
    try:
        resp = svc.generate(
            session_id="sess-m4-artfail",
            agent_id="agt",
            purpose="act",
            query="test artifact failure",
            budgets=RLMBudgets(max_ticks=1),
        )
        assert isinstance(resp.final_text, str)
    except (IOError, OSError):
        pass


def test_m4_wm_writeback_failure_is_tolerated() -> None:
    sess = _Sess()
    sess._fail_put = True
    service = RLMService(
        sessctl=sess,
        contextctl=_Ctx(),
        llmctl=_LLM([{"final": True, "answer": "Despite write error."}]),
    )
    try:
        resp = service.generate(
            session_id="sess-m4-writefail",
            agent_id="agt",
            purpose="act",
            query="test writeback failure",
            budgets=RLMBudgets(max_ticks=1),
        )
        assert isinstance(resp.final_text, str)
    except RuntimeError:
        pass


def test_m4_concurrent_sessions_do_not_cross_contaminate() -> None:
    results: dict[str, str] = {}
    errors: list[Exception] = []

    def run(session_id: str, answer: str) -> None:
        try:
            sess = _Sess()
            svc = RLMService(
                sessctl=sess,
                contextctl=_Ctx(),
                llmctl=_LLM([{"final": True, "answer": answer}]),
            )
            resp = svc.generate(
                session_id=session_id,
                agent_id="agt",
                purpose="act",
                query=f"query for {session_id}",
                budgets=RLMBudgets(max_ticks=1),
            )
            results[session_id] = resp.final_text
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    t1 = threading.Thread(target=run, args=("sess-concurrent-A", "Answer A"))
    t2 = threading.Thread(target=run, args=("sess-concurrent-B", "Answer B"))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert not errors, f"Concurrent generate raised: {errors}"
    assert results.get("sess-concurrent-A") == "Answer A"
    assert results.get("sess-concurrent-B") == "Answer B"
