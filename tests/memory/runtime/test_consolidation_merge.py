from __future__ import annotations

from dataclasses import dataclass
from threading import Lock, Thread
import time

from openminion.modules.llm.providers.factory import RuntimeLLMHandle
from openminion.modules.llm.schemas import LLMResponse
from openminion.modules.memory.runtime.consolidation.coordinator import (
    ConsolidationConfig,
    ExtractionPayload,
)
from openminion.modules.memory.runtime.consolidation.merge import (
    resolve_consolidation_model_handle,
    run_consolidation_merge,
)


class _FakeMergeClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def complete(self, messages, tools=None, **overrides):  # noqa: ANN001
        self.calls.append(
            {
                "messages": list(messages),
                "tools": tools,
                "overrides": dict(overrides),
            }
        )
        return LLMResponse(
            ok=True,
            provider="echo",
            model=str(overrides.get("model") or "echo"),
            output_text="reviewed",
            memory_consolidation={
                "decisions": [
                    {
                        "candidate_id": "cand-1",
                        "action": "promote",
                        "reasoning": "durable lesson",
                    },
                    {
                        "candidate_id": "cand-2",
                        "action": "discard",
                        "reasoning": "duplicate",
                    },
                    {
                        "candidate_id": "cand-3",
                        "action": "defer",
                        "reasoning": "need more evidence",
                    },
                ]
            },
            assistant_messages=[],
            tool_calls=[],
        )


def _payload() -> ExtractionPayload:
    return ExtractionPayload(
        session_id="session-1",
        agent_id="agent-1",
        candidate_refs=[
            {"candidate_id": "cand-1"},
            {"candidate_id": "cand-2"},
            {"candidate_id": "cand-3"},
        ],
        evidence_window={"recent_rollout_limit": 256},
    )


def test_resolve_consolidation_model_handle_returns_distinct_model_handle() -> None:
    client = _FakeMergeClient()
    primary = RuntimeLLMHandle(name="openai", model="gpt-4.2", client=client)

    resolved = resolve_consolidation_model_handle(
        primary,
        ConsolidationConfig(consolidation_model="gpt-4.2-mini"),
    )

    assert resolved is not primary
    assert resolved.model == "gpt-4.2-mini"
    assert resolved.client is client
    assert resolved.name == primary.name


def test_run_consolidation_merge_returns_review_vocabulary_without_writes() -> None:
    client = _FakeMergeClient()
    handle = RuntimeLLMHandle(name="openai", model="gpt-4.2-mini", client=client)

    result = run_consolidation_merge(_payload(), handle)

    assert result.model_name == "gpt-4.2-mini"
    assert [item.action for item in result.decisions] == [
        "promote",
        "discard",
        "defer",
    ]
    assert client.calls[0]["overrides"]["tool_choice"] == "none"
    assert client.calls[0]["tools"] is None


@dataclass
class _ConcurrencyProbe:
    active: int = 0
    max_active: int = 0
    call_count: int = 0


class _BlockingMergeClient:
    def __init__(self, probe: _ConcurrencyProbe, guard: Lock) -> None:
        self._probe = probe
        self._guard = guard

    def complete(self, messages, tools=None, **overrides):  # noqa: ANN001
        del messages, tools, overrides
        with self._guard:
            self._probe.call_count += 1
            self._probe.active += 1
            self._probe.max_active = max(self._probe.max_active, self._probe.active)
        time.sleep(0.05)
        with self._guard:
            self._probe.active -= 1
        return LLMResponse(
            ok=True,
            provider="echo",
            model="gpt-4.2-mini",
            output_text="reviewed",
            memory_consolidation={
                "decisions": [
                    {
                        "candidate_id": "cand-1",
                        "action": "defer",
                        "reasoning": "serialized",
                    }
                ]
            },
            assistant_messages=[],
            tool_calls=[],
        )


def test_run_consolidation_merge_serializes_same_session_agent_calls() -> None:
    probe = _ConcurrencyProbe()
    guard = Lock()
    client = _BlockingMergeClient(probe, guard)
    handle = RuntimeLLMHandle(name="openai", model="gpt-4.2-mini", client=client)
    payload = _payload()
    results: list[object] = []

    def _worker() -> None:
        results.append(run_consolidation_merge(payload, handle))

    first = Thread(target=_worker)
    second = Thread(target=_worker)
    first.start()
    second.start()
    first.join()
    second.join()

    assert probe.call_count == 2
    assert probe.max_active == 1
    assert len(results) == 2
