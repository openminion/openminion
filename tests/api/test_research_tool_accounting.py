"""Public response/persistence acceptance for research child tools (#531).

Routing and child work are deterministic; the real research mode, turn
postprocessing, gateway and database closeout run without a provider.
"""

import json

from openminion import Agent, APIRuntime
from openminion.base.config import OpenMinionConfig, save_config
from openminion.modules.brain.runner import BrainRunner
from openminion.modules.brain.schemas import ActDecision, ActionResult
from openminion.modules.brain.loop.strategies.research.handler import ResearchMode
from openminion.modules.brain.loop.strategies.research.schemas import ResearchSynthesis
from tests._csc_fixtures import _csc_install_default_agent
from types import SimpleNamespace


def test_public_research_tool_totals_and_persistence(tmp_path, monkeypatch):
    config = OpenMinionConfig()
    _csc_install_default_agent(config, provider="echo")
    config.runtime.log_level = "ERROR"
    config.storage.path = str(tmp_path / "state.db")
    config_path = tmp_path / "agents.json"
    save_config(config, str(config_path))
    monkeypatch.setattr(
        BrainRunner,
        "_decide",
        lambda *a, **kw: ActDecision(
            act_profile="research",
            confidence=1.0,
            reason_code="test_research",
        ),
    )
    monkeypatch.setattr(
        ResearchMode,
        "_build_synthesis",
        lambda *a, **kw: ResearchSynthesis(answer="local synthesis"),
    )
    records_by_trace = {}

    def invoke(runner, **kwargs):
        child = kwargs["state"]
        records = records_by_trace.setdefault(child.trace_id, [])
        record = {
            "call_id": f"{child.trace_id}-{len(records)}",
            "tool_name": "file.read",
            "turn_scope_id": child.trace_id,
            "ok": bool(len(records) % 2),
            "content": "local evidence",
        }
        records.append(record)
        child.budgets_remaining.tool_calls -= 1
        result = ActionResult(
            command_id=record["call_id"],
            status="success",
            summary="evidence",
            outputs={"tool_results": [record]},
        )
        return SimpleNamespace(
            status="done", message="evidence", action_result=result, working_state=child
        )

    monkeypatch.setattr(
        "openminion.modules.brain.loop.strategies.research.handler.invoke_decision_direct",
        invoke,
    )
    runtime = APIRuntime.from_config_path(
        str(config_path), home_root=tmp_path, data_root=tmp_path / "data"
    )
    try:
        with Agent(runtime=runtime, session_id="research-stats") as agent:
            results = [
                agent.run("Research local evidence A"),
                agent.run("Research local evidence B"),
            ]
        for result in results:
            count = int(result.metadata["tool_calls_count"])
            assert count > 0
            assert result.stats["tool_calls"] == count
            assert result.stats["tool_errors"] == (count + 1) // 2
            assert json.loads(result.metadata["run_stats_json"])["tool_calls"] == count
            messages = runtime.sessions.list_messages(
                session_id=result.session_id, limit=100
            )
            persisted = next(
                m
                for m in messages
                if m.role == "outbound" and m.metadata.get("run_id") == result.run_id
            )
            assert json.loads(persisted.metadata["run_stats_json"]) == result.stats
            assert persisted.metadata["tool_calls_count"] == str(count)
            assert count == len(next(iter(records_by_trace.values())))
        assert len(records_by_trace) == 2

    finally:
        runtime.close()


def test_real_child_tool_execution_matches_public_and_durable_records(
    tmp_path, monkeypatch
):
    from openminion import tool
    from openminion.modules.brain.loop.tools import DefaultAdaptiveToolLoopLLMRuntime
    from openminion.modules.llm.schemas import LLMResponse, ToolCall, UsageInfo

    config = OpenMinionConfig()
    _csc_install_default_agent(config, provider="echo")
    config.runtime.log_level = "ERROR"
    config.storage.path = str(tmp_path / "state.db")
    config_path = tmp_path / "agents.json"
    save_config(config, str(config_path))
    turn_sources = []

    def decide(runner, *, state, **kwargs):
        turn_sources.append(
            (runner.session_api.store, state.session_id, state.trace_id)
        )
        return ActDecision(
            act_profile="research", confidence=1.0, reason_code="test_research"
        )

    monkeypatch.setattr(BrainRunner, "_decide", decide)
    monkeypatch.setattr(
        ResearchMode,
        "_build_synthesis",
        lambda *a, **kw: ResearchSynthesis(answer="local synthesis"),
    )
    executed = []

    @tool(idempotent=True, min_scope="READ_ONLY")
    def local_evidence() -> str:
        """Read a deterministic local evidence fact."""
        executed.append(1)
        return "LOCAL_FACT=731"

    def complete(self, *, messages, tools, model, **kwargs):
        if not any(m.role == "tool" for m in messages):
            assert any(spec.name == "local_evidence" for spec in tools)
            return LLMResponse(
                ok=True,
                provider="fake",
                model=model,
                tool_calls=[
                    ToolCall(
                        id=f"read-{len(executed)}", name="local_evidence", arguments={}
                    )
                ],
                finish_reason="tool_calls",
                usage=UsageInfo(input_tokens=8, output_tokens=2),
            )
        return LLMResponse(
            ok=True,
            provider="fake",
            model=model,
            output_text="LOCAL_FACT=731",
            finalization_status={
                "status": "final_answer",
                "reasoning": "evidence read",
            },
            finish_reason="stop",
            usage=UsageInfo(input_tokens=8, output_tokens=2),
        )

    monkeypatch.setattr(DefaultAdaptiveToolLoopLLMRuntime, "complete", complete)
    runtime = APIRuntime.from_config_path(
        str(config_path), home_root=tmp_path, data_root=tmp_path / "data"
    )
    try:
        with Agent(runtime=runtime, tools=[local_evidence]) as agent:
            result = agent.run("Research the local evidence", timeout_seconds=30)
        assert executed
        store, session_id, trace_id = turn_sources[0]
        canonical = [
            event
            for event in store.list_events(session_id, limit=10000)
            if event.get("payload", {}).get("turn_scope_id") == trace_id
        ]
        requested = [
            event["payload"]["call_id"]
            for event in canonical
            if event["type"] == "tool.call.requested"
        ]
        completed = [
            event["payload"]["call_id"]
            for event in canonical
            if event["type"] == "tool.call.completed"
        ]
        assert sorted(requested) == sorted(completed)
        assert len(set(completed)) == len(executed)
        assert result.stats["tool_calls"] == len(executed)
        assert int(result.metadata["tool_calls_count"]) == len(executed)
        assert json.loads(result.metadata["run_stats_json"])["tool_calls"] == len(
            executed
        )
        messages = runtime.sessions.list_messages(
            session_id=result.session_id, limit=100
        )
        persisted = next(m for m in messages if m.role == "outbound")
        assert json.loads(persisted.metadata["run_stats_json"]) == result.stats
    finally:
        runtime.close()
