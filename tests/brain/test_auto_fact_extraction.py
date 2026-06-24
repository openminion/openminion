from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from openminion.modules.brain.adapters.context import LocalContextAdapter
from openminion.modules.brain.adapters.memory import LocalMemoryAdapter
from openminion.modules.brain.adapters.session import LocalSessionStore
from openminion.modules.brain.config import RunnerOptions
from openminion.modules.brain.execution.memory import extract_user_message_candidates
from openminion.modules.brain.runner.tick.context import TickRunContext
from openminion.modules.brain.runner.tick.input_processing import process_user_input
from openminion.modules.brain.schemas import (
    AgentBudgets,
    AgentDefaults,
    AgentProfile,
    BudgetCounters,
    LLMProfiles,
    WorkingState,
)


class _Logger:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict, dict]] = []

    def emit(self, event_type: str, payload: dict, **kwargs: Any) -> None:
        self.events.append((event_type, payload, kwargs))


class _MockLLM:
    def __init__(
        self,
        *,
        report: dict[str, Any] | None = None,
        raise_exc: Exception | None = None,
    ) -> None:
        self._report = report
        self._raise = raise_exc
        self.calls: list[dict[str, Any]] = []

    def estimate_tokens(self, *, model: str, context: dict[str, Any]) -> int:
        del model, context
        return 50

    def call_structured(
        self,
        *,
        model: str,
        purpose: str,
        context: dict[str, Any],
        schema: type,
    ) -> dict[str, Any]:
        self.calls.append(
            {"model": model, "purpose": purpose, "schema": schema.__name__}
        )
        if self._raise is not None:
            raise self._raise
        if self._report is None:
            return {"session_id": "s1", "agent_id": "a1", "items": []}
        return self._report


def _profile() -> AgentProfile:
    return AgentProfile(
        agent_id="afe-agent",
        role="general",
        llm_profiles=LLMProfiles(
            decide_model="decide-default",
            plan_model="plan-default",
            act_model=None,
            reflect_model="reflect-default",
            summarize_model="summarize-default",
        ),
        budgets=AgentBudgets(
            max_ticks_per_user_turn=10,
            max_tool_calls=5,
            max_a2a_calls=0,
            max_total_llm_tokens=5000,
            max_elapsed_ms=60_000,
        ),
        defaults=AgentDefaults(),
    )


def _state() -> WorkingState:
    return WorkingState(
        session_id="afe-session",
        agent_id="afe-agent",
        goal="hello",
        budgets_remaining=BudgetCounters(
            ticks=5,
            tool_calls=5,
            a2a_calls=0,
            tokens=5000,
            time_ms=60_000,
        ),
        trace_id="trace-afe-1",
    )


def _runner(
    tmp_path: Path,
    *,
    llm_api: Any,
    afe_config: Any = None,
) -> SimpleNamespace:
    session_store = LocalSessionStore(tmp_path / "sessions")
    context_api = LocalContextAdapter(session_store=session_store)
    memory_api = LocalMemoryAdapter(tmp_path / "memory")

    def _build_context(*, state, purpose, budget, hints, logger):
        del logger
        return context_api.build(
            session_id=state.session_id,
            agent_id=state.agent_id,
            purpose=purpose,
            budget=budget,
            hints=hints,
        )

    options = RunnerOptions()

    profile = _profile()
    if afe_config is not None:
        profile_payload = profile.model_dump(mode="python")
        profile_payload["auto_fact_extraction"] = afe_config
        profile = SimpleNamespace(
            **{k: v for k, v in profile_payload.items() if k != "auto_fact_extraction"}
        )
        profile.llm_profiles = _profile().llm_profiles
        profile.auto_fact_extraction = afe_config
        profile.agent_id = "afe-agent"

    return SimpleNamespace(
        profile=profile,
        options=options,
        llm_api=llm_api,
        context_api=context_api,
        session_api=session_store,
        memory_api=memory_api,
        _build_context=_build_context,
        _debit_tokens=lambda *args, **kwargs: None,
    )


def _candidate_payloads(tmp: str) -> list[dict[str, Any]]:
    jsonl = Path(tmp) / "memory" / "memory.jsonl"
    if not jsonl.exists():
        return []
    lines = jsonl.read_text(encoding="utf-8").splitlines()
    payloads = [json.loads(line) for line in lines if line.strip()]
    return [payload for payload in payloads if payload.get("kind") == "candidate"]


# focused unit tests


def test_afe_report_schema_accepts_model_omitting_transport_ids() -> None:
    from openminion.modules.brain.schemas import UserMessageCandidateReport

    # Valid: items only, no session_id / agent_id.
    r = UserMessageCandidateReport.model_validate({"items": []})
    assert r.session_id is None
    assert r.agent_id is None
    # Still valid if the model echoes them back.
    r2 = UserMessageCandidateReport.model_validate(
        {"session_id": "s1", "agent_id": "a1", "items": []}
    )
    assert r2.session_id == "s1"


def test_process_user_input_skips_afe_for_explicit_tool_commands() -> None:
    with TemporaryDirectory() as tmp:
        runner = _runner(Path(tmp), llm_api=_MockLLM())
        logger = _Logger()
        state = _state()
        tick_ctx = TickRunContext(
            session_id=state.session_id,
            user_input='tool weather {"location":"san francisco"}',
            original_user_input='tool weather {"location":"san francisco"}',
            skip_initial_interpret=True,
        )

        with (
            patch(
                "openminion.modules.brain.execution.extract_user_message_candidates"
            ) as extract_mock,
            patch(
                "openminion.modules.brain.runner.tick.input_processing._runner_delegate",
                return_value=None,
            ),
        ):
            process_user_input(
                runner=runner,
                state=state,
                logger=logger,
                tick_ctx=tick_ctx,
            )

        extract_mock.assert_not_called()


def test_afe_stages_when_model_omits_transport_ids() -> None:
    report = {
        "items": [
            {
                "kind": "fact",
                "normalized_key": "fact:user_name",
                "title": "user name",
                "content": "Jay",
                "tags": [],
            }
        ]
    }
    with TemporaryDirectory() as tmp:
        runner = _runner(Path(tmp), llm_api=_MockLLM(report=report))
        logger = _Logger()

        ids = extract_user_message_candidates(
            runner,
            state=_state(),
            user_message="my name is Jay",
            logger=logger,
        )

        assert len(ids) == 1
        candidates = _candidate_payloads(tmp)
        assert candidates[0]["meta"]["source_session_id"] == "afe-session"
        assert candidates[0]["meta"]["source_agent_id"] == "afe-agent"


def test_afe_stages_candidates_from_user_message() -> None:
    report = {
        "session_id": "afe-session",
        "agent_id": "afe-agent",
        "items": [
            {
                "kind": "fact",
                "normalized_key": "fact:user_name",
                "title": "user name",
                "content": "Jay",
                "tags": [],
            },
            {
                "kind": "user_preference",
                "normalized_key": "user_preference:language",
                "title": "preferred language",
                "content": "TypeScript",
                "tags": [],
            },
        ],
    }
    with TemporaryDirectory() as tmp:
        runner = _runner(Path(tmp), llm_api=_MockLLM(report=report))
        logger = _Logger()

        ids = extract_user_message_candidates(
            runner,
            state=_state(),
            user_message="my name is Jay and I prefer TypeScript",
            logger=logger,
        )

        assert len(ids) == 2
        candidates = _candidate_payloads(tmp)
        assert len(candidates) == 2
        record_types = {c["record_type"] for c in candidates}
        assert record_types == {"fact", "user_preference"}
        assert all(c["meta"]["source"] == "auto_extracted" for c in candidates)
        assert all(c["meta"]["source_agent_id"] == "afe-agent" for c in candidates)
        # AFE-fixed initial confidence (0.3) — not whatever the model said.
        assert all(c["confidence"] == 0.3 for c in candidates)
        assert any(
            event_type == "brain.auto_fact_extraction.completed"
            for event_type, _p, _k in logger.events
        )


def test_afe_skips_when_user_message_too_short() -> None:
    with TemporaryDirectory() as tmp:
        runner = _runner(Path(tmp), llm_api=_MockLLM(report=None))
        logger = _Logger()

        ids = extract_user_message_candidates(
            runner,
            state=_state(),
            user_message="hi",
            logger=logger,
        )

        assert ids == []
        assert any(
            event_type == "brain.auto_fact_extraction.skipped"
            and payload.get("reason") == "user_message_too_short"
            for event_type, payload, _kw in logger.events
        )
        assert _candidate_payloads(tmp) == []


def test_afe_graceful_degradation_on_llm_failure() -> None:
    with TemporaryDirectory() as tmp:
        runner = _runner(
            Path(tmp),
            llm_api=_MockLLM(raise_exc=RuntimeError("provider timeout")),
        )
        logger = _Logger()

        ids = extract_user_message_candidates(
            runner,
            state=_state(),
            user_message="my name is Jay and I prefer TypeScript",
            logger=logger,
        )

        assert ids == []
        skipped = [
            (payload.get("reason"), payload.get("error"))
            for event_type, payload, _kw in logger.events
            if event_type == "brain.auto_fact_extraction.skipped"
        ]
        assert any(reason == "extraction_failed" for reason, _err in skipped)
        # Memory persistence was not touched on LLM failure.
        assert _candidate_payloads(tmp) == []


def test_afe_caps_items_per_turn() -> None:
    report = {
        "session_id": "afe-session",
        "agent_id": "afe-agent",
        "items": [
            {
                "kind": "fact",
                "normalized_key": f"fact:item_{idx}",
                "title": f"item {idx}",
                "content": f"content {idx}",
                "tags": [],
            }
            for idx in range(12)
        ],
    }

    class _CapConfig:
        enabled = True
        model_tier = "reflect"
        max_items_per_turn = 3
        min_user_message_chars = 1

    with TemporaryDirectory() as tmp:
        runner = _runner(
            Path(tmp),
            llm_api=_MockLLM(report=report),
            afe_config=_CapConfig(),
        )
        logger = _Logger()

        ids = extract_user_message_candidates(
            runner,
            state=_state(),
            user_message="a long message with many facts",
            logger=logger,
        )

        assert len(ids) == 3
        completed = [
            payload
            for event_type, payload, _kw in logger.events
            if event_type == "brain.auto_fact_extraction.completed"
        ]
        assert len(completed) == 1
        assert completed[0]["extracted_items"] == 12
        assert completed[0]["staged_candidates"] == 3


def test_afe_uses_agent_scope_by_default() -> None:
    report = {
        "session_id": "afe-session",
        "agent_id": "afe-agent",
        "items": [
            {
                "kind": "fact",
                "normalized_key": "fact:user_name",
                "title": "user name",
                "content": "Jay",
                "tags": [],
            }
        ],
    }
    with TemporaryDirectory() as tmp:
        runner = _runner(Path(tmp), llm_api=_MockLLM(report=report))
        logger = _Logger()

        extract_user_message_candidates(
            runner,
            state=_state(),
            user_message="my name is Jay",
            logger=logger,
        )

        candidates = _candidate_payloads(tmp)
        assert len(candidates) == 1
        assert candidates[0]["scope"] == "agent:afe-agent"


def test_afe_rebuilds_invalid_normalized_key() -> None:
    # Model proposes an invalid key — runtime rebuilds a deterministic one.
    report = {
        "session_id": "afe-session",
        "agent_id": "afe-agent",
        "items": [
            {
                "kind": "fact",
                "normalized_key": "NOT A VALID KEY",
                "title": "user name",
                "content": "Jay",
                "tags": [],
            }
        ],
    }
    with TemporaryDirectory() as tmp:
        runner = _runner(Path(tmp), llm_api=_MockLLM(report=report))
        logger = _Logger()

        extract_user_message_candidates(
            runner,
            state=_state(),
            user_message="my name is Jay",
            logger=logger,
        )

        candidates = _candidate_payloads(tmp)
        assert len(candidates) == 1
        key = candidates[0]["meta"]["normalized_key"]
        # Rebuilt key must be valid bounded shape.
        assert key.startswith("fact:")
        assert ":" in key
        assert "NOT" not in key  # original invalid key discarded


def test_afe_skips_when_memory_api_unavailable() -> None:
    with TemporaryDirectory() as tmp:
        runner = _runner(Path(tmp), llm_api=_MockLLM())
        runner.memory_api = None
        logger = _Logger()

        ids = extract_user_message_candidates(
            runner,
            state=_state(),
            user_message="my name is Jay",
            logger=logger,
        )

        assert ids == []
        assert any(
            event_type == "brain.auto_fact_extraction.skipped"
            and payload.get("reason") == "memory_api_unavailable"
            for event_type, payload, _kw in logger.events
        )


def test_afe_skips_when_disabled() -> None:
    class _DisabledConfig:
        enabled = False

    with TemporaryDirectory() as tmp:
        runner = _runner(
            Path(tmp),
            llm_api=_MockLLM(),
            afe_config=_DisabledConfig(),
        )
        logger = _Logger()

        ids = extract_user_message_candidates(
            runner,
            state=_state(),
            user_message="my name is Jay",
            logger=logger,
        )

        assert ids == []
        assert any(
            event_type == "brain.auto_fact_extraction.skipped"
            and payload.get("reason") == "disabled"
            for event_type, payload, _kw in logger.events
        )
