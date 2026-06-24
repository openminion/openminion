from __future__ import annotations

import json
from typing import Any

from openminion.modules.brain.loop.recursive.schemas import MetaDirective, RLMBudgets
from openminion.modules.brain.loop.recursive.service import RLMService


class FakeSession:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def get_latest_working_state(self, session_id: str) -> dict[str, Any] | None:
        return None

    def put_working_state(
        self,
        session_id: str,
        *,
        state_ref: str | None = None,
        state_inline: dict[str, Any] | None = None,
    ) -> int:
        return 1

    def append_event(
        self,
        session_id: str,
        type: str | None = None,
        payload: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> str:
        self.events.append({"type": type, "payload": payload or {}})
        return f"ev-{len(self.events)}"


class FakeCtx:
    def build_pack(self, request: Any) -> dict[str, Any]:
        return {"pack_version": "v1", "messages": []}


class FakeRetrieval:
    def __init__(self, quality: str = "GOOD") -> None:
        self.quality = quality

    def retrieve(self, **kwargs: Any) -> list[Any]:
        from openminion.modules.brain.loop.recursive.schemas import RetrievedContext

        score = (
            0.9 if self.quality == "GOOD" else (0.6 if self.quality == "OK" else 0.2)
        )
        return [
            RetrievedContext(
                source="em",
                ref_id=f"art-{self.quality}",
                text=f"Sample text {self.quality}",
                score=score,
            )
        ]


class FakeLLM:
    def __init__(self, payloads: list[dict[str, Any]]) -> None:
        self.payloads = payloads
        self.call_count = 0

    def call_for_agent(
        self,
        agent_id: str,
        purpose: str,
        request: dict[str, Any],
        agent_policy: dict[str, Any],
    ) -> dict[str, Any]:
        payload = self.payloads[min(self.call_count, len(self.payloads) - 1)]
        self.call_count += 1
        return {
            "status": "success",
            "text": json.dumps(payload),
            "json_output": payload,
            "usage": {"prompt_tokens": 10, "completion_tokens": 10},
        }


def test_integration_budget_exhaustion() -> None:
    # M4: Failure-mode test for budget exhaustion
    llm = FakeLLM(
        payloads=[{"final": False, "answer": "Thinking...", "next_query": "more"}]
    )
    session = FakeSession()
    service = RLMService(
        sessctl=session,
        contextctl=FakeCtx(),
        llmctl=llm,
        retrievectl=FakeRetrieval("GOOD"),
    )

    # Force max ticks to 2
    budgets = RLMBudgets(max_ticks=2)
    res = service.generate(
        session_id="s1", agent_id="a1", purpose="act", query="test", budgets=budgets
    )

    assert res.telemetry.ticks_used == 2
    assert res.telemetry.stop_reason == "max_ticks_reached"
    assert res.continuation is not None
    assert res.continuation.needs_more_ticks is True
    assert llm.call_count == 2

    # Verify events were recorded
    started_events = [e for e in session.events if e["type"] == "rlm.tick.started"]
    completed_events = [e for e in session.events if e["type"] == "rlm.tick.completed"]
    assert len(started_events) == 2
    assert len(completed_events) == 2
    assert completed_events[-1]["payload"]["stop_reason"] == "max_ticks_reached"


def test_integration_bad_retrieval_streak() -> None:
    # M4: Failure-mode test for bad retrieval streak escalation
    llm = FakeLLM(
        payloads=[
            {"final": False, "answer": "Not enough info", "next_query": "try again"}
        ]
    )
    session = FakeSession()
    service = RLMService(
        sessctl=session,
        contextctl=FakeCtx(),
        llmctl=llm,
        retrievectl=FakeRetrieval("BAD"),
    )

    # Set bad retrieval streak tolerance to 2
    meta = MetaDirective(max_bad_retrieval_streak=2)
    budgets = RLMBudgets(
        max_ticks=5
    )  # Plenty of ticks, but should stop early due to bad retrieval

    res = service.generate(
        session_id="s1",
        agent_id="a1",
        purpose="act",
        query="test",
        meta_directive=meta,
        budgets=budgets,
    )

    assert res.telemetry.ticks_used == 2
    assert res.telemetry.stop_reason == "retrieval_quality_bad_streak"
    assert res.continuation is not None
    assert res.continuation.needs_more_ticks is True
    assert llm.call_count == 2


def test_integration_successful_recursive_completion() -> None:
    # M3: Integration test for successful multi-tick completion
    llm = FakeLLM(
        payloads=[
            {
                "final": False,
                "answer": "Step 1 done. Next step.",
                "next_query": "step 2",
            },
            {"final": True, "answer": "All done.", "citations": ["art-GOOD"]},
        ]
    )
    session = FakeSession()
    service = RLMService(
        sessctl=session,
        contextctl=FakeCtx(),
        llmctl=llm,
        retrievectl=FakeRetrieval("GOOD"),
    )

    res = service.generate(
        session_id="s1", agent_id="a1", purpose="act", query="do a multi step task"
    )

    assert res.telemetry.ticks_used == 2
    assert res.telemetry.stop_reason == "model_marked_final"
    assert res.continuation is not None
    assert res.continuation.needs_more_ticks is False
    assert res.final_text == "All done."
    assert llm.call_count == 2
