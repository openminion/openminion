from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from openminion.modules.brain.loop.adaptive import _direct_tool_turn_context
from openminion.modules.brain.loop.adaptive import ActLoopMode
from openminion.modules.brain.loop.tools import (
    ADAPTIVE_TERM_DECOMPOSE_REQUESTED,
    AdaptiveToolLoopOutcome,
    AdaptiveToolLoopState,
)
from openminion.modules.brain.trailers import (
    EXPECTED_TRAILERS_METADATA_KEY,
    TRAILER_LANE_MACC,
    TRAILER_LANE_PTC,
)
from openminion.modules.brain.execution.loop_contracts import (
    ExecutionContext,
    ExecutionResult,
)
from openminion.modules.brain.execution.entry import (
    _copied_seeded_commands,
    _seeded_sub_intent_ids,
)
from openminion.modules.brain.bootstrap.resolve import build_general_act_decision
from openminion.modules.brain.loop.entry import (
    coding_tool_spec,
    decompose_tool_spec,
    detect_entry_path,
    extract_response_text,
    research_tool_spec,
)
from openminion.modules.brain.loop.strategies.coding import build_coding_decision
from openminion.modules.brain.loop.strategies.research import build_research_decision
from openminion.modules.brain.runner import RunnerOptions, BrainRunner
from openminion.modules.brain.schemas import (
    ActDecision,
    AgentBudgets,
    AgentDefaults,
    AgentProfile,
    BudgetCounters,
    LLMProfiles,
    Plan,
    ToolCommand,
    WorkingState,
)
from openminion.modules.brain.adapters.a2a import LocalA2AAdapter
from openminion.modules.brain.adapters.context import LocalContextAdapter
from openminion.modules.brain.adapters.memory import LocalMemoryAdapter
from openminion.modules.brain.adapters.policy import LocalPolicyAdapter
from openminion.modules.brain.adapters.session import LocalSessionStore
from tests.brain.runner_test_support import fake_command_executor, fake_logger

from openminion.modules.llm.schemas import (
    LLMRequest,
    LLMResponse,
    Message,
    ToolCall,
    UsageInfo,
)

_SAMPLE_README_PATH = "/workspace/README.md"


def _profile(default_act_profile: str | None = None) -> AgentProfile:
    return AgentProfile(
        agent_id="entry-agent",
        role="general",
        llm_profiles=LLMProfiles(
            decide_model="decide-default",
            plan_model="plan-default",
            act_model=None,
            reflect_model="reflect-default",
            summarize_model="summarize-default",
        ),
        default_act_profile=default_act_profile,
        budgets=AgentBudgets(
            max_ticks_per_user_turn=5,
            max_tool_calls=4,
            max_a2a_calls=2,
            max_total_llm_tokens=4000,
            max_elapsed_ms=60_000,
        ),
        defaults=AgentDefaults(),
    )


def _state(session_id: str = "s-entry") -> WorkingState:
    return WorkingState(
        session_id=session_id,
        agent_id="entry-agent",
        trace_id=f"trace-{session_id}",
        budgets_remaining=BudgetCounters(
            ticks=5,
            tool_calls=4,
            a2a_calls=2,
            tokens=4000,
            time_ms=60_000,
        ),
        llm_calls_max=5,
    )


def _text_response(text: str) -> LLMResponse:
    return LLMResponse(
        ok=True,
        provider="test",
        model="decide-default",
        output_text=text,
        assistant_messages=[Message(role="assistant", content=text)],
        tool_calls=[],
        usage=UsageInfo(total_tokens=1),
        finish_reason="stop",
    )


def _tool_response(name: str, arguments: dict[str, object]) -> LLMResponse:
    return LLMResponse(
        ok=True,
        provider="test",
        model="decide-default",
        output_text="",
        assistant_messages=[Message(role="assistant", content="")],
        tool_calls=[ToolCall(name=name, arguments=arguments)],
        usage=UsageInfo(total_tokens=1),
        finish_reason="tool_calls",
    )


def test_copied_seeded_commands_preserves_full_batch() -> None:
    first = ToolCommand(
        title="write pyproject",
        tool_name="file.write",
        args={"path": "demo/pyproject.toml", "body": "[project]"},
    )
    second = ToolCommand(
        title="write readme",
        tool_name="file.write",
        args={"path": "demo/README.md", "body": "# demo"},
    )

    copied = _copied_seeded_commands([first, second])

    assert [command.args["path"] for command in copied] == [
        "demo/pyproject.toml",
        "demo/README.md",
    ]
    assert copied[0] is not first
    assert copied[1] is not second


def test_seeded_sub_intent_ids_collects_all_unique_batch_intents() -> None:
    first = ToolCommand(
        title="write pyproject",
        tool_name="file.write",
        args={"path": "demo/pyproject.toml", "body": "[project]"},
        sub_intent_ids=["setup", "scaffold"],
    )
    second = ToolCommand(
        title="write readme",
        tool_name="file.write",
        args={"path": "demo/README.md", "body": "# demo"},
        sub_intent_ids=["scaffold", "docs"],
    )

    assert _seeded_sub_intent_ids([first, second]) == ["setup", "scaffold", "docs"]


def test_build_general_act_decision_preserves_entry_response() -> None:
    response = _tool_response("file.write", {"path": "/tmp/demo.txt", "content": "x"})
    decision = ActDecision(reason_code="entry_tool_call", act_profile="general")
    decision._entry_response = response

    internal = build_general_act_decision(decision=decision)

    assert getattr(internal, "_entry_response", None) is response


def test_build_coding_and_research_decisions_preserve_entry_response() -> None:
    response = _tool_response("file.write", {"path": "/tmp/demo.txt", "content": "x"})
    decision = ActDecision(reason_code="entry_tool_call", act_profile="general")
    decision._entry_response = response

    coding_internal = build_coding_decision(decision=decision, goal="build project")
    research_internal = build_research_decision(
        decision=decision,
        query="research packaging",
    )

    assert getattr(coding_internal, "_entry_response", None) is response
    assert getattr(research_internal, "_entry_response", None) is response


class _RecordingEntryLLM:
    def __init__(self, response: LLMResponse) -> None:
        self.response = response
        self.requests: list[LLMRequest] = []

    def estimate_tokens(self, *, model: str, context: dict[str, object]) -> int:
        _ = model, context
        return 1

    def call(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        return self.response


def _build_runner(
    tmp_path: Path,
    *,
    llm_api,
    default_act_profile: str | None = None,
) -> BrainRunner:
    session = LocalSessionStore(tmp_path / "sessions")
    return BrainRunner(
        profile=_profile(default_act_profile),
        session_api=session,
        context_api=LocalContextAdapter(session_store=session),
        llm_api=llm_api,
        tool_api=SimpleNamespace(registry=SimpleNamespace(_tools={})),
        a2a_api=LocalA2AAdapter(),
        memory_api=LocalMemoryAdapter(tmp_path / "memory"),
        policy_api=LocalPolicyAdapter(),
        options=RunnerOptions(metactl_enabled=False),
    )


def test_path_detection_uses_only_structural_signals() -> None:
    respond = detect_entry_path(_text_response("Do you mean UTC or local time?"))
    clarify = detect_entry_path(
        _tool_response("clarify", {"question": "Which timezone do you mean?"})
    )
    act = detect_entry_path(_tool_response("time", {"timezone": "UTC"}))

    assert respond.path == "respond"
    assert extract_response_text(_text_response("Do you mean UTC or local time?"))
    assert respond.response_text == "Do you mean UTC or local time?"
    assert clarify.path == "clarify"
    assert clarify.clarify_question == "Which timezone do you mean?"
    assert act.path == "act"
    assert act.tool_call_names == ("time",)


def test_decompose_tool_contract_excludes_single_research_threads() -> None:
    spec = decompose_tool_spec()

    assert "independent subtasks" in spec.description
    assert "single deep-research thread" in spec.description


def test_research_tool_contract_targets_iterative_research_threads() -> None:
    spec = research_tool_spec()

    assert "iterative research loop" in spec.description
    assert "multiple searches" in spec.description


def test_coding_tool_contract_targets_iterative_verified_software_work() -> None:
    spec = coding_tool_spec()

    assert "dedicated coding loop" in spec.description
    assert "tests" in spec.description
    assert "final verification" in spec.description


def test_unified_entry_time_prompt_prefers_explicit_tool_sequence(
    tmp_path: Path,
) -> None:
    llm = _RecordingEntryLLM(_text_response("It is currently 07:12 UTC."))
    runner = _build_runner(tmp_path, llm_api=llm)
    state = _state("entry-respond")

    decision = runner._decide(
        state=state,
        user_input="what time is it in UTC?",
        logger=fake_logger(),
    )

    assert decision.mode == "act"
    assert decision.reason_code == "explicit_tool_sequence"
    assert decision.answer is None
    assert len(llm.requests) == 1
    tool_names = [tool.name for tool in list(llm.requests[0].tools or [])]
    assert "clarify" in tool_names
    assert "tool.request" in tool_names
    assert "respond" in tool_names
    assert "time" not in tool_names


def test_unified_entry_coding_category_exposes_only_coding_and_clarify(
    tmp_path: Path,
) -> None:
    llm = _RecordingEntryLLM(_text_response("What kind of app should I scaffold?"))
    runner = _build_runner(tmp_path, llm_api=llm)
    state = _state("entry-coding-category")

    decision = runner._decide(
        state=state,
        user_input="create a tiny python project",
        logger=fake_logger(),
        capability_category="coding",
    )

    assert decision.mode == "respond"
    assert len(llm.requests) == 1
    tool_names = [tool.name for tool in list(llm.requests[0].tools or [])]
    assert set(tool_names) == {"clarify", "coding"}
    assert len(tool_names) == 2


def test_unified_entry_respond_path_carries_expected_trailers_metadata(
    tmp_path: Path,
    monkeypatch,
) -> None:
    response = _text_response("Done.")
    response = response.model_copy(
        update={"confident_complete": {"complete": True, "reasoning": "done"}}
    )
    llm = _RecordingEntryLLM(response)
    runner = _build_runner(tmp_path, llm_api=llm)
    monkeypatch.setattr(
        runner,
        "_resolve_skill_hints",
        lambda **kwargs: {EXPECTED_TRAILERS_METADATA_KEY: [TRAILER_LANE_MACC]},
    )
    state = _state("entry-respond-metadata")

    decision = runner._decide(
        state=state,
        user_input="finish this",
        logger=fake_logger(),
    )

    assert decision.mode == "respond"
    assert llm.requests[0].metadata[EXPECTED_TRAILERS_METADATA_KEY] == [
        TRAILER_LANE_MACC
    ]
    events = runner.session_api.list_events(state.session_id)
    expected_event = next(
        event for event in events if event["type"] == "trailer.expected"
    )
    emitted_event = next(
        event for event in events if event["type"] == "trailer.emitted"
    )
    assert expected_event["payload"] == {
        "lanes": [TRAILER_LANE_MACC],
        "route": "direct_respond",
    }
    assert emitted_event["payload"] == {
        "lanes": [TRAILER_LANE_MACC],
        "route": "direct_respond",
        "sources": {TRAILER_LANE_MACC: ["structured_field"]},
    }


def test_unified_entry_clarify_path_returns_waiting_question(tmp_path: Path) -> None:
    llm = _RecordingEntryLLM(
        _tool_response("clarify", {"question": "Which city should I use?"})
    )
    runner = _build_runner(tmp_path, llm_api=llm)
    state = _state("entry-clarify")

    decision = runner._decide(
        state=state,
        user_input="what's the weather today?",
        logger=fake_logger(),
    )

    assert decision.mode == "respond"
    assert decision.respond_kind == "clarify"
    assert decision.reason_code == "entry_clarify"
    assert decision.question == "Which city should I use?"


def test_unified_entry_act_path_attaches_seed_response(tmp_path: Path) -> None:
    response = _tool_response("time", {"timezone": "UTC"})
    llm = _RecordingEntryLLM(response)
    runner = _build_runner(tmp_path, llm_api=llm)
    state = _state("entry-act")

    decision = runner._decide(
        state=state,
        user_input="what time is it in UTC?",
        logger=fake_logger(),
    )

    assert decision.mode == "act"
    assert decision.reason_code == "entry_tool_call"
    assert getattr(decision, "_entry_response", None) is response
    route = getattr(decision, "_pre_resolved_act_route", None)
    assert route is not None
    assert getattr(route, "act_profile", "") == "general"
    assert getattr(getattr(route, "execution_target", None), "kind", "") == "local"


def test_unified_entry_coding_control_routes_to_coding_profile(tmp_path: Path) -> None:
    response = _tool_response("coding", {})
    llm = _RecordingEntryLLM(response)
    runner = _build_runner(tmp_path, llm_api=llm)
    state = _state("entry-coding-control")

    decision = runner._decide(
        state=state,
        user_input="create a tiny python project and run tests",
        logger=fake_logger(),
    )

    assert decision.mode == "act"
    assert decision.reason_code == "entry_coding_tool_call"
    assert getattr(decision, "_entry_response", None) is None
    route = getattr(decision, "_pre_resolved_act_route", None)
    assert route is not None
    assert getattr(route, "act_profile", "") == "coding"
    assert getattr(getattr(route, "execution_target", None), "kind", "") == "local"


def test_unified_entry_file_write_seed_routes_to_coding_profile(
    tmp_path: Path,
) -> None:
    response = _tool_response(
        "file.write",
        {"path": str(tmp_path / "sample.py"), "content": "print('ok')\n"},
    )
    llm = _RecordingEntryLLM(response)
    runner = _build_runner(tmp_path, llm_api=llm)
    state = _state("entry-file-write-coding")

    decision = runner._decide(
        state=state,
        user_input="create a tiny python project",
        logger=fake_logger(),
    )

    assert decision.mode == "act"
    assert decision.reason_code == "entry_coding_seed_tool_call"
    assert getattr(decision, "_entry_response", None) is response
    route = getattr(decision, "_pre_resolved_act_route", None)
    assert route is not None
    assert getattr(route, "act_profile", "") == "coding"
    assert getattr(route, "source", "") == "entry_mutation_seed_tool_call"


def test_unified_entry_readonly_seed_routes_explicit_file_artifact_to_coding(
    tmp_path: Path,
) -> None:
    response = _tool_response("file.list_dir", {"path": "."})
    llm = _RecordingEntryLLM(response)
    runner = _build_runner(tmp_path, llm_api=llm)
    state = _state("entry-readonly-file-artifact")

    decision = runner._decide(
        state=state,
        user_input=(
            "Implement a tiny package with module code, CLI entry, tests, and "
            "README using file.write/file.read."
        ),
        logger=fake_logger(),
    )

    assert decision.mode == "act"
    assert decision.reason_code == "entry_coding_user_file_artifact_request"
    assert getattr(decision, "_entry_response", None) is response
    route = getattr(decision, "_pre_resolved_act_route", None)
    assert route is not None
    assert getattr(route, "act_profile", "") == "coding"
    assert getattr(route, "source", "") == "entry_user_file_artifact_request"


def test_unified_entry_tool_request_for_file_artifact_does_not_seed_coding(
    tmp_path: Path,
) -> None:
    response = _tool_response("tool.request", {"name": "file.write"})
    llm = _RecordingEntryLLM(response)
    runner = _build_runner(tmp_path, llm_api=llm)
    state = _state("entry-tool-request-file-artifact")

    decision = runner._decide(
        state=state,
        user_input=(
            "Build a small package using file.write and file.read; write "
            "module code, tests, and README files."
        ),
        logger=fake_logger(),
    )

    assert decision.mode == "act"
    assert decision.reason_code == "entry_coding_user_file_artifact_request"
    assert getattr(decision, "_entry_response", None) is None
    route = getattr(decision, "_pre_resolved_act_route", None)
    assert route is not None
    assert getattr(route, "act_profile", "") == "coding"
    assert getattr(route, "source", "") == "entry_user_file_artifact_request"


def test_entry_decompose_with_one_subtask_routes_to_orchestrate(
    tmp_path: Path,
) -> None:
    response = _tool_response(
        "decompose",
        {"subtasks": [{"id": "s1", "description": "Inspect the repository"}]},
    )
    llm = _RecordingEntryLLM(response)
    runner = _build_runner(tmp_path, llm_api=llm)
    state = _state("entry-decompose-one")

    decision = runner._decide(
        state=state,
        user_input="break this down",
        logger=fake_logger(),
    )

    assert decision.mode == "act"
    assert decision.reason_code == "entry_decompose_tool_call"
    assert getattr(decision, "_entry_response", None) is None
    route = getattr(decision, "_pre_resolved_act_route", None)
    assert route is not None
    assert getattr(route, "act_profile", "") == "orchestrate"
    assert getattr(getattr(route, "execution_target", None), "kind", "") == "local"
    assert decision.subtasks == [
        {
            "subtask_id": "s1",
            "goal": "Inspect the repository",
            "inputs": {},
            "depends_on": [],
            "suggested_mode": None,
            "priority": 0,
        }
    ]


def test_entry_research_tool_routes_to_research_profile(tmp_path: Path) -> None:
    response = _tool_response("research", {})
    llm = _RecordingEntryLLM(response)
    runner = _build_runner(tmp_path, llm_api=llm)
    state = _state("entry-research")

    decision = runner._decide(
        state=state,
        user_input="do deep research on the latest iran news before answering",
        logger=fake_logger(),
    )

    assert decision.mode == "act"
    assert decision.reason_code == "entry_research_tool_call"
    route = getattr(decision, "_pre_resolved_act_route", None)
    assert route is not None
    assert getattr(route, "act_profile", "") == "research"
    assert getattr(getattr(route, "execution_target", None), "kind", "") == "local"


def test_entry_research_mixed_tool_calls_fail_closed_without_inference(
    tmp_path: Path,
) -> None:
    response = LLMResponse(
        ok=True,
        provider="test",
        model="decide-default",
        output_text="",
        assistant_messages=[Message(role="assistant", content="")],
        tool_calls=[
            ToolCall(name="research", arguments={}),
            ToolCall(name="web.search", arguments={"query": "latest iran news"}),
        ],
        usage=UsageInfo(total_tokens=1),
        finish_reason="tool_calls",
    )
    llm = _RecordingEntryLLM(response)
    runner = _build_runner(tmp_path, llm_api=llm)
    state = _state("entry-research-mixed")

    decision = runner._decide(
        state=state,
        user_input="do deep research on latest iran news",
        logger=fake_logger(),
    )

    assert decision.mode == "respond"
    assert decision.reason_code == "entry_research_mixed_tool_calls"
    assert getattr(decision, "_entry_response", None) is None


def test_entry_decompose_with_multiple_subtasks_routes_to_orchestrate(
    tmp_path: Path,
) -> None:
    response = _tool_response(
        "decompose",
        {
            "subtasks": [
                {
                    "id": "s1",
                    "description": "Inspect the repository",
                    "inputs": {"path": "openminion"},
                },
                {
                    "id": "s2",
                    "description": "Summarize the findings",
                    "depends_on": ["s1"],
                    "priority": 1,
                },
            ]
        },
    )
    llm = _RecordingEntryLLM(response)
    runner = _build_runner(tmp_path, llm_api=llm)
    state = _state("entry-decompose-many")

    decision = runner._decide(
        state=state,
        user_input="break this down",
        logger=fake_logger(),
    )

    route = getattr(decision, "_pre_resolved_act_route", None)
    assert decision.reason_code == "entry_decompose_tool_call"
    assert getattr(route, "act_profile", "") == "orchestrate"
    assert [item["subtask_id"] for item in decision.subtasks] == ["s1", "s2"]
    assert decision.subtasks[0]["inputs"] == {"path": "openminion"}
    assert decision.subtasks[1]["depends_on"] == ["s1"]
    assert decision.subtasks[1]["priority"] == 1


def test_entry_decompose_empty_subtasks_declines_without_orchestrate(
    tmp_path: Path,
) -> None:
    response = _tool_response("decompose", {"subtasks": []})
    llm = _RecordingEntryLLM(response)
    runner = _build_runner(tmp_path, llm_api=llm)
    state = _state("entry-decompose-empty")

    decision = runner._decide(
        state=state,
        user_input="break this down",
        logger=fake_logger(),
    )

    assert decision.mode == "act"
    assert decision.reason_code == "entry_decompose_declined"
    assert getattr(decision, "_entry_response", None) is None
    route = getattr(decision, "_pre_resolved_act_route", None)
    assert route is not None
    assert getattr(route, "act_profile", "") != "orchestrate"
    assert decision.subtasks == []


def test_entry_decompose_malformed_payload_fails_closed_without_routing(
    tmp_path: Path,
) -> None:
    response = _tool_response("decompose", {"subtasks": [{"id": "s1"}]})
    llm = _RecordingEntryLLM(response)
    runner = _build_runner(tmp_path, llm_api=llm)
    state = _state("entry-decompose-malformed")

    decision = runner._decide(
        state=state,
        user_input="break this down",
        logger=fake_logger(),
    )

    assert decision.mode == "respond"
    assert decision.reason_code == "entry_decompose_invalid_payload"
    assert getattr(decision, "_pre_resolved_act_route", None) is None


def test_entry_decompose_mixed_tool_calls_fail_closed_without_inference(
    tmp_path: Path,
) -> None:
    response = LLMResponse(
        ok=True,
        provider="test",
        model="decide-default",
        output_text="",
        assistant_messages=[Message(role="assistant", content="")],
        tool_calls=[
            ToolCall(name="decompose", arguments={"subtasks": []}),
            ToolCall(name="time", arguments={"timezone": "UTC"}),
        ],
        usage=UsageInfo(total_tokens=1),
        finish_reason="tool_calls",
    )
    llm = _RecordingEntryLLM(response)
    runner = _build_runner(tmp_path, llm_api=llm)
    state = _state("entry-decompose-mixed")

    decision = runner._decide(
        state=state,
        user_input="break this down",
        logger=fake_logger(),
    )

    assert decision.mode == "respond"
    assert decision.reason_code == "entry_decompose_mixed_tool_calls"
    assert getattr(decision, "_entry_response", None) is None


def test_config_fixed_orchestrate_accepts_explicit_entry_decompose_payload(
    tmp_path: Path,
) -> None:
    response = _tool_response(
        "decompose",
        {
            "subtasks": [
                {"id": "s1", "description": "Inspect the current state"},
                {"id": "s2", "description": "Report findings", "depends_on": ["s1"]},
            ]
        },
    )
    llm = _RecordingEntryLLM(response)
    runner = _build_runner(tmp_path, llm_api=llm, default_act_profile="orchestrate")
    state = _state("entry-decompose-config-orchestrate")

    decision = runner._decide(
        state=state,
        user_input="use orchestration if needed",
        logger=fake_logger(),
    )

    route = getattr(decision, "_pre_resolved_act_route", None)
    assert decision.reason_code == "entry_decompose_tool_call"
    assert getattr(route, "act_profile", "") == "orchestrate"
    assert [item["subtask_id"] for item in decision.subtasks] == ["s1", "s2"]


def test_config_fixed_orchestrate_empty_decompose_decline_falls_back_to_general(
    tmp_path: Path,
) -> None:
    response = _tool_response("decompose", {"subtasks": []})
    llm = _RecordingEntryLLM(response)
    runner = _build_runner(tmp_path, llm_api=llm, default_act_profile="orchestrate")
    state = _state("entry-decompose-config-decline")

    decision = runner._decide(
        state=state,
        user_input="use orchestration if needed",
        logger=fake_logger(),
    )

    route = getattr(decision, "_pre_resolved_act_route", None)
    assert decision.reason_code == "entry_decompose_declined"
    assert decision.subtasks == []
    assert getattr(route, "act_profile", "") == "general"


def test_persisted_orchestrate_state_bypasses_new_entry_decompose_payload(
    tmp_path: Path,
) -> None:
    response = _tool_response(
        "decompose",
        {"subtasks": [{"id": "new", "description": "Replace persisted state"}]},
    )
    llm = _RecordingEntryLLM(response)
    runner = _build_runner(tmp_path, llm_api=llm, default_act_profile="orchestrate")
    state = _state("entry-decompose-persisted")
    state.child_task_order = ["existing"]

    decision = runner._decide(
        state=state,
        user_input="continue",
        logger=fake_logger(),
    )

    route = getattr(decision, "_pre_resolved_act_route", None)
    assert decision.reason_code == "bootstrap_resolved_workflow_entry_bypass"
    assert getattr(route, "act_profile", "") == "orchestrate"
    assert llm.requests == []


def test_unified_entry_act_path_carries_expected_trailers_metadata(
    tmp_path: Path,
    monkeypatch,
) -> None:
    response = _tool_response("time", {"timezone": "UTC"})
    response = response.model_copy(
        update={
            "pending_turn_context": {
                "original_user_request": "check time",
                "active_work_summary": "entry routed to time tool",
            }
        }
    )
    llm = _RecordingEntryLLM(response)
    runner = _build_runner(tmp_path, llm_api=llm)
    monkeypatch.setattr(
        runner,
        "_resolve_skill_hints",
        lambda **kwargs: {EXPECTED_TRAILERS_METADATA_KEY: [TRAILER_LANE_PTC]},
    )
    state = _state("entry-act-metadata")

    decision = runner._decide(
        state=state,
        user_input="what time is it in UTC?",
        logger=fake_logger(),
    )

    assert decision.mode == "act"
    assert llm.requests[0].metadata[EXPECTED_TRAILERS_METADATA_KEY] == [
        TRAILER_LANE_PTC
    ]
    events = runner.session_api.list_events(state.session_id)
    expected_event = next(
        event for event in events if event["type"] == "trailer.expected"
    )
    emitted_event = next(
        event for event in events if event["type"] == "trailer.emitted"
    )
    assert expected_event["payload"] == {
        "lanes": [TRAILER_LANE_PTC],
        "route": "entry_act",
    }
    assert emitted_event["payload"] == {
        "lanes": [TRAILER_LANE_PTC],
        "route": "entry_act",
        "sources": {TRAILER_LANE_PTC: ["structured_field"]},
    }


def test_fixed_coding_profile_constrains_entry_tool_surface(tmp_path: Path) -> None:
    llm = _RecordingEntryLLM(_text_response("I'll inspect the repo files."))
    runner = _build_runner(tmp_path, llm_api=llm, default_act_profile="coding")
    state = _state("entry-coding")

    decision = runner._decide(
        state=state,
        user_input="inspect the repo and patch the bug",
        logger=fake_logger(),
    )

    assert decision.mode == "respond"
    tool_names = {tool.name for tool in list(llm.requests[0].tools or [])}
    assert "clarify" in tool_names
    assert "tool.request" in tool_names
    assert "file.read" not in tool_names
    assert "web.search" not in tool_names
    assert "time" not in tool_names


def test_entry_respond_tool_carries_freshness_without_auxiliary_call(
    tmp_path: Path,
) -> None:
    llm = _RecordingEntryLLM(
        _tool_response(
            "respond",
            {
                "answer": "Hi there.",
                "freshness": {
                    "intent": "greeting",
                    "domain": "general",
                    "time_sensitive": False,
                    "needs_live_data": False,
                    "needs_sources": False,
                    "needs_exact_date": False,
                    "answer_mode": "local_only",
                    "reason": "No current facts are needed.",
                    "confidence": 0.99,
                },
            },
        )
    )
    runner = _build_runner(tmp_path, llm_api=llm)
    state = _state("entry-typed-freshness")

    decision = runner._decide(
        state=state,
        user_input="hi",
        logger=fake_logger(),
    )

    assert decision.mode == "respond"
    assert decision.answer == "Hi there."
    assert state.freshness_contract is not None
    assert state.freshness_diagnostics is not None
    assert state.freshness_diagnostics.classifier_mode == "entry_contract"
    assert len(llm.requests) == 1


def test_plan_resume_bypasses_unified_entry_call(tmp_path: Path) -> None:
    llm = _RecordingEntryLLM(_text_response("unused"))
    runner = _build_runner(tmp_path, llm_api=llm)
    state = _state("entry-resume")
    state.plan = Plan(
        objective="resume UTC lookup",
        steps=[
            ToolCommand(
                title="Check time",
                tool_name="time",
                args={"timezone": "UTC"},
                success_criteria={"status": "success"},
            )
        ],
        stop_conditions=[],
        assumptions=[],
        risk_summary="",
        success_criteria={},
    )
    state.cursor = 0

    decision = runner._decide(
        state=state,
        user_input=None,
        logger=fake_logger(),
    )

    assert decision.mode == "act"
    assert decision.reason_code == "resume_existing_plan"
    assert len(getattr(decision, "_seeded_commands", []) or []) == 1
    assert llm.requests == []


def test_direct_tool_turn_context_activates_when_user_explicitly_names_tool(
    tmp_path: Path,
) -> None:
    llm = _RecordingEntryLLM(_text_response("unused"))
    runner = _build_runner(tmp_path, llm_api=llm)
    state = _state("entry-direct-tool-mention")
    ctx = SimpleNamespace(
        state=state,
        decision=SimpleNamespace(
            reason_code="entry_text_response", _seeded_commands=[]
        ),
        user_input=(
            f"tool file.read on {_SAMPLE_README_PATH} "
            "and reply with the first sentence only"
        ),
        _services=SimpleNamespace(runner=runner),
    )
    seed_response = _tool_response("file.read", {"path": _SAMPLE_README_PATH})

    direct_turn = _direct_tool_turn_context(ctx=ctx, seed_response=seed_response)

    assert direct_turn is not None
    assert direct_turn.requested_tool_names == ("file.read",)


def test_direct_tool_turn_context_activates_name_only_without_seed_tool_call(
    tmp_path: Path,
) -> None:
    llm = _RecordingEntryLLM(_text_response("unused"))
    runner = _build_runner(tmp_path, llm_api=llm)
    state = _state("entry-direct-tool-name-only")
    ctx = SimpleNamespace(
        state=state,
        decision=SimpleNamespace(
            reason_code="entry_text_response", _seeded_commands=[]
        ),
        user_input=(
            f"tool file.read on {_SAMPLE_README_PATH} "
            "and reply with the first sentence only"
        ),
        _services=SimpleNamespace(runner=runner),
    )
    seed_response = _text_response("I don't have that tool available.")

    direct_turn = _direct_tool_turn_context(ctx=ctx, seed_response=seed_response)

    assert direct_turn is not None
    assert direct_turn.requested_tool_names == ("file.read",)
    assert direct_turn.match_by_name_only is False
    assert direct_turn.requested_calls


def test_direct_tool_turn_context_does_not_activate_for_natural_language_tool_hint(
    tmp_path: Path,
) -> None:
    llm = _RecordingEntryLLM(_text_response("unused"))
    runner = _build_runner(tmp_path, llm_api=llm)
    state = _state("entry-direct-tool-weather-repeat")
    ctx = SimpleNamespace(
        state=state,
        decision=SimpleNamespace(
            reason_code="entry_text_response", _seeded_commands=[]
        ),
        user_input=(
            "Please use the weather tool to get the current weather in San Francisco, "
            "then answer with one concise sentence."
        ),
        _services=SimpleNamespace(runner=runner),
    )

    direct_turn = _direct_tool_turn_context(ctx=ctx, seed_response=None)

    assert direct_turn is None


def test_direct_tool_turn_context_collapses_repeated_single_tool_mentions_for_explicit_syntax(
    tmp_path: Path,
) -> None:
    llm = _RecordingEntryLLM(_text_response("unused"))
    runner = _build_runner(tmp_path, llm_api=llm)
    state = _state("entry-direct-tool-weather-repeat-explicit")
    ctx = SimpleNamespace(
        state=state,
        decision=SimpleNamespace(
            reason_code="entry_text_response", _seeded_commands=[]
        ),
        user_input=(
            "tool weather then weather for current weather in San Francisco, "
            "then answer with one concise sentence."
        ),
        _services=SimpleNamespace(runner=runner),
    )

    direct_turn = _direct_tool_turn_context(ctx=ctx, seed_response=None)

    assert direct_turn is not None
    assert direct_turn.requested_tool_names == ("weather",)
    assert direct_turn.match_by_name_only is False
    assert direct_turn.requested_calls


def test_direct_tool_turn_context_does_not_activate_for_plain_weather_question(
    tmp_path: Path,
) -> None:
    llm = _RecordingEntryLLM(_text_response("unused"))
    runner = _build_runner(tmp_path, llm_api=llm)
    state = _state("entry-plain-weather-question")
    ctx = SimpleNamespace(
        state=state,
        decision=SimpleNamespace(
            reason_code="entry_text_response", _seeded_commands=[]
        ),
        user_input="hey what's weather today?",
        _services=SimpleNamespace(runner=runner),
    )
    seed_response = _tool_response("weather", {"location": "san francisco"})

    direct_turn = _direct_tool_turn_context(ctx=ctx, seed_response=seed_response)

    assert direct_turn is None


def test_decide_skips_freshness_classifier_for_direct_tool_request(
    tmp_path: Path,
) -> None:
    llm = _RecordingEntryLLM(
        _tool_response(
            "file.read",
            {"path": _SAMPLE_README_PATH},
        )
    )
    runner = _build_runner(tmp_path, llm_api=llm)
    state = _state("entry-direct-tool-freshness-skip")

    with patch(
        "openminion.modules.brain.bootstrap.freshness_classify.call_structured_with_retry",
        side_effect=AssertionError(
            "freshness classifier should not make an LLM call for direct tool requests"
        ),
    ):
        decision = runner._decide(
            state=state,
            user_input=(
                f"use file.read on {_SAMPLE_README_PATH} "
                "and reply with the first sentence only"
            ),
            logger=fake_logger(),
        )

    assert decision.mode == "act"
    assert decision.reason_code == "entry_tool_call"
    assert len(llm.requests) == 1


def test_budget_guard_skips_entry_call_when_llm_budget_exhausted(
    tmp_path: Path,
) -> None:
    llm = _RecordingEntryLLM(_text_response("unused"))
    runner = _build_runner(tmp_path, llm_api=llm)
    state = _state("entry-budget")
    state.llm_calls_used = state.llm_calls_max

    decision = runner._decide(
        state=state,
        user_input="what time is it in UTC?",
        logger=fake_logger(),
    )

    assert decision.mode == "respond"
    assert decision.reason_code == "llm_call_budget_exceeded"
    assert llm.requests == []


def test_t0_direct_entry_exposes_only_clarify_tool(tmp_path: Path) -> None:
    llm = _RecordingEntryLLM(_text_response("Please clarify your request."))
    runner = _build_runner(tmp_path, llm_api=llm)
    state = _state("entry-tier0")
    state.tier = "T0_direct"

    decision = runner._decide(
        state=state,
        user_input="what time is it in UTC?",
        logger=fake_logger(),
    )

    assert decision.mode == "respond"
    tool_names = [tool.name for tool in list(llm.requests[0].tools or [])]
    assert tool_names == ["clarify"]


def test_act_loop_uses_seed_response_from_entry_call() -> None:
    captured: dict[str, object] = {}
    decision = ActDecision(reason_code="entry_tool_call", act_profile="general")
    seed_response = _tool_response("time", {"timezone": "UTC"})
    decision._entry_response = seed_response

    ctx = ExecutionContext(
        state=_state("entry-seeded-loop"),
        decision=decision,
        user_input="what time is it in UTC?",
        logger=fake_logger(),
        options=SimpleNamespace(profile=None),
        llm_adapter=SimpleNamespace(client=_RecordingEntryLLM(seed_response)),
        command_executor=fake_command_executor(),
        _services=SimpleNamespace(runner=None),
    )

    def _fake_run_adaptive_tool_loop(*args, **kwargs):
        captured["seed_response"] = kwargs.get("seed_response")
        return SimpleNamespace(
            mode_result=ExecutionResult(
                status="done",
                working_state=ctx.state,
                message="ok",
            )
        )

    with patch(
        "openminion.modules.brain.loop.adaptive.run_adaptive_tool_loop",
        side_effect=_fake_run_adaptive_tool_loop,
    ):
        result = ActLoopMode().execute(ctx)

    assert result.status == "done"
    assert captured["seed_response"] is seed_response


def test_act_loop_routes_mid_loop_decompose_outcome_to_orchestrate() -> None:
    captured: dict[str, object] = {}
    decision = ActDecision(reason_code="entry_tool_call", act_profile="general")
    state = _state("mid-loop-decompose")
    subtasks = [
        {"subtask_id": "s1", "goal": "Inspect files"},
        {"subtask_id": "s2", "goal": "Summarize findings", "depends_on": ["s1"]},
    ]
    ctx = ExecutionContext(
        state=state,
        decision=decision,
        user_input="continue and split if useful",
        logger=fake_logger(),
        options=SimpleNamespace(profile=None),
        llm_adapter=SimpleNamespace(
            client=_RecordingEntryLLM(_text_response("unused"))
        ),
        command_executor=fake_command_executor(),
        _services=SimpleNamespace(runner=None, emit_phase_status=lambda **kwargs: None),
    )

    def _fake_run_adaptive_tool_loop(*args, **kwargs):
        del args
        captured["tool_specs"] = kwargs.get("tool_specs")
        return AdaptiveToolLoopOutcome(
            profile_name="general_adaptive_v1",
            mode_name="act_adaptive",
            termination_reason=ADAPTIVE_TERM_DECOMPOSE_REQUESTED,
            state=AdaptiveToolLoopState(
                scratchpad={"adaptive.decompose_subtasks": subtasks}
            ),
            allowed_tools=frozenset({"decompose"}),
            decompose_subtasks=subtasks,
        )

    def _fake_orchestrate_execute(self, orchestrate_ctx):
        del self
        captured["decision"] = orchestrate_ctx.decision
        return ExecutionResult(
            status="done",
            working_state=orchestrate_ctx.state,
            message="orchestrated",
        )

    with (
        patch(
            "openminion.modules.brain.loop.adaptive.run_adaptive_tool_loop",
            side_effect=_fake_run_adaptive_tool_loop,
        ),
        patch(
            "openminion.modules.brain.execution.orchestrate.handler.OrchestrateMode.execute",
            side_effect=_fake_orchestrate_execute,
            autospec=True,
        ),
    ):
        result = ActLoopMode().execute(ctx)

    assert result.status == "done"
    routed_decision = captured["decision"]
    assert "decompose" in {
        getattr(spec, "name", "") for spec in list(captured["tool_specs"] or [])
    }
    assert getattr(routed_decision, "act_profile", "") == "orchestrate"
    assert getattr(routed_decision, "reason_code", "") == "mid_loop_decompose_tool_call"
    assert list(getattr(routed_decision, "subtasks", []) or []) == subtasks
