from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

import pytest

from openminion.modules.brain.adapters.a2a import LocalA2AAdapter
from openminion.modules.brain.adapters.context import LocalContextAdapter
from openminion.modules.brain.adapters.memory import LocalMemoryAdapter
from openminion.modules.brain.adapters.policy import LocalPolicyAdapter
from openminion.modules.brain.adapters.session import LocalSessionStore
from openminion.modules.brain.adapters.tool import LocalToolAdapter
from openminion.modules.brain.runner import RunnerOptions, BrainRunner
from openminion.modules.brain.schemas import (
    AgentBudgets,
    AgentDefaults,
    AgentProfile,
    LLMProfiles,
)
from openminion.modules.brain.diagnostics.telemetry import emit_brain_operation
from openminion.modules.context.compress.compaction import CompactionService
from openminion.modules.context.compress.strategies import DeltaEvent
from openminion.modules.context.compress.events import emit_compress_operation
from openminion.modules.llm import LLMCTL
from openminion.modules.llm.schemas import LLMRequest, LLMResponse, Message, UsageInfo
from openminion.modules.llm.diagnostics.events import emit_llm_operation
from openminion.modules.memory.models import MemoryRecord
from openminion.modules.memory.service import MemoryService
from openminion.modules.memory.storage.base import ListQueryOptions, SearchQueryOptions
from openminion.modules.memory.storage.memory import InMemoryMemoryStore
from openminion.modules.memory.diagnostics.events import emit_memory_operation
from openminion.modules.retrieve.runtime.retrieve import RetrieveCtl
from openminion.modules.retrieve.diagnostics.events import (
    emit_retrieve_operation,
)
from openminion.services.runtime.manager import (
    AgentRuntimeManager,
    TurnRequest,
    TurnResponse,
    TurnTelemetry,
)
from openminion.services.runtime.events import emit_runtime_operation
from openminion.modules.brain.adapters.session import SessctlAdapter
from openminion.modules.session.diagnostics.events import (
    emit_session_operation,
)
from openminion.modules.skill.runtime.skill import Skill
from openminion.modules.skill.diagnostics.events import emit_skill_operation
from openminion.modules.tool.runtime.policy import Policy
from openminion.modules.tool.runtime import RuntimeContext
from openminion.modules.tool.diagnostics.events import (
    emit_tool_exec_operation,
    emit_tool_invoke_operation,
)
from openminion.tools.exec.plugin import _h_exec_run


class _FailingTelemetryCtl:
    def emit_module_operation(self, *args: Any, **kwargs: Any) -> None:  # noqa: ANN401
        del args, kwargs
        raise RuntimeError("telemetry sink unavailable")

    def emit_module_counter(self, *args: Any, **kwargs: Any) -> None:  # noqa: ANN401
        del args, kwargs
        raise RuntimeError("telemetry sink unavailable")

    def emit_module_stats(self, *args: Any, **kwargs: Any) -> None:  # noqa: ANN401
        del args, kwargs
        raise RuntimeError("telemetry sink unavailable")


class _SequenceProvider:
    name = "telemetry-safety-provider"
    contract_version = "v1"

    def complete(self, request: LLMRequest, config: dict[str, Any]) -> LLMResponse:
        del request, config
        return LLMResponse(
            ok=True,
            provider=self.name,
            model="telemetry-model",
            output_text="ok",
            assistant_messages=[Message(role="assistant", content="ok")],
            tool_calls=[],
            usage=UsageInfo(input_tokens=5, output_tokens=2, total_tokens=7),
            latency_ms=5,
            finish_reason="stop",
            provider_raw=None,
            error=None,
            telemetry={},
        )

    def list_models(self, config: dict[str, Any]) -> list[str]:
        del config
        return ["telemetry-model"]

    def healthcheck(self, config: dict[str, Any]) -> dict[str, Any]:
        del config
        return {"ok": True}


class _DummyLogger:
    def emit(self, *args: Any, **kwargs: Any) -> None:
        del args, kwargs


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _build_runtime_context(tmp_path: Path, telemetryctl: Any | None) -> RuntimeContext:
    run_root = tmp_path / "run"
    run_root.mkdir(parents=True, exist_ok=True)
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    return RuntimeContext(
        policy=Policy(
            raw={
                "workspace_root": str(tmp_path / "runs"),
                "paths": {
                    "read_allow": [str(workspace)],
                    "write_allow": [str(workspace)],
                    "deny": [],
                },
                "commands": {
                    "mode": "allowlist",
                    "allow": ["bash", "zsh", "sh", "echo", "pwd", "cat"],
                    "deny_exact": [],
                    "deny_regex": [],
                },
                "env": {"allow_keys": ["PATH", "HOME"], "deny_keys_regex": []},
            }
        ),
        workspace=workspace,
        run_root=run_root,
        scope="WRITE_SAFE",
        confirm=False,
        telemetryctl=telemetryctl,
        telemetry_session_id="sess-safety",
        telemetry_turn_id="turn-1",
    )


def _brain_profile() -> AgentProfile:
    return AgentProfile(
        agent_id="brain-safety-agent",
        role="general",
        llm_profiles=LLMProfiles(
            decide_model="decide-default",
            plan_model="plan-default",
            act_model=None,
            reflect_model="reflect-default",
            summarize_model="summarize-default",
        ),
        budgets=AgentBudgets(
            max_ticks_per_user_turn=4,
            max_tool_calls=2,
            max_a2a_calls=1,
            max_total_llm_tokens=1000,
            max_elapsed_ms=10000,
        ),
        defaults=AgentDefaults(),
    )


def _make_llm_runtime(*, telemetryctl: Any | None) -> LLMCTL:
    runtime = LLMCTL.from_config(
        {
            "version": 1,
            "llmctl": {
                "default_provider": "telemetry-safety-provider",
                "default_model": "telemetry-model",
                "retries": {"max_retries": 0, "backoff_ms": 0},
            },
            "providers": {"telemetry-safety-provider": {}},
            "agents": {
                "default": {
                    "default_provider": "telemetry-safety-provider",
                    "default_model": "telemetry-model",
                }
            },
        },
        telemetryctl=telemetryctl,
    )
    runtime.registry.add(_SequenceProvider())
    return runtime


def _skill_config(tmp_path: Path) -> dict[str, Any]:
    return {
        "skill": {
            "sqlite_path": str(tmp_path / "skill.db"),
            "wal": False,
            "default_status_filter": ["draft", "verified", "blessed"],
            "high_risk_status_filter": ["blessed", "verified"],
            "known_tools": ["tool.shell", "tool.log"],
        }
    }


def _retrieve_config(tmp_path: Path) -> dict[str, Any]:
    return {
        "version": 1,
        "retrievectl": {
            "storage": {
                "sqlite_path": str(tmp_path / "retrievectl.db"),
                "blob_root": str(tmp_path / "blob"),
                "wal_mode": False,
            },
            "defaults": {
                "strategy": "contextual",
                "contextual_enabled": True,
                "embeddings_enabled": False,
                "lexical_candidate_count": 25,
                "snippet_tokens": 120,
                "chunk_target_tokens": 30,
                "chunk_min_tokens": 15,
                "chunk_max_tokens": 35,
                "doc_group_target_tokens": 40,
                "doc_group_min_tokens": 25,
                "doc_group_max_tokens": 60,
                "raptor_internal_k": 2,
                "raptor_leaf_k": 4,
            },
        },
    }


HELPER_CASES = [
    lambda ctl: emit_llm_operation(
        telemetryctl=ctl,
        session_id="sess-safety",
        turn_id="turn-1",
        operation="request",
        provider="local",
        model="test",
    ),
    lambda ctl: emit_tool_exec_operation(
        telemetryctl=ctl,
        session_id="sess-safety",
        turn_id="turn-1",
        operation="run",
        tool_name="exec.run",
    ),
    lambda ctl: emit_tool_invoke_operation(
        telemetryctl=ctl,
        session_id="sess-safety",
        turn_id="turn-1",
        operation="invoke",
        tool_name="exec.run",
    ),
    lambda ctl: emit_compress_operation(
        telemetryctl=ctl,
        session_id="sess-safety",
        turn_id="turn-1",
        operation="summary_create",
    ),
    lambda ctl: emit_skill_operation(
        telemetryctl=ctl,
        session_id="sess-safety",
        turn_id="turn-1",
        operation="shortlist",
    ),
    lambda ctl: emit_memory_operation(
        telemetryctl=ctl,
        session_id="sess-safety",
        turn_id="turn-1",
        operation="query",
    ),
    lambda ctl: emit_retrieve_operation(
        telemetryctl=ctl,
        session_id="sess-safety",
        turn_id="turn-1",
        operation="query",
    ),
    lambda ctl: emit_runtime_operation(
        telemetryctl=ctl,
        session_id="sess-safety",
        turn_id="turn-1",
        operation="turn_start",
    ),
    lambda ctl: emit_brain_operation(
        telemetryctl=ctl,
        session_id="sess-safety",
        turn_id="turn-1",
        operation="turn_start",
    ),
    lambda ctl: emit_session_operation(
        telemetryctl=ctl,
        session_id="sess-safety",
        turn_id="turn-1",
        operation="turn_start",
    ),
]


def test_module_helpers_log_warning_when_sink_fails(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING)
    ctl = _FailingTelemetryCtl()
    for call in HELPER_CASES:
        assert call(ctl) is False
    assert any("telemetry emit failed" in record.message for record in caplog.records)


@pytest.mark.parametrize(
    "telemetryctl, expect_warning",
    [
        pytest.param(None, False, id="disabled"),
        pytest.param(_FailingTelemetryCtl(), True, id="sink_failure"),
    ],
)
def test_module_flows_continue_without_telemetry_crashes(
    telemetryctl: Any | None,
    expect_warning: bool,
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    caplog.set_level(logging.WARNING)

    llm_runtime = _make_llm_runtime(telemetryctl=telemetryctl)
    llm_response = _run(
        llm_runtime.client(agent_name="default").call(
            {
                "messages": [{"role": "user", "content": "hello"}],
                "metadata": {
                    "session_id": "sess-safety",
                    "turn_id": "turn-1",
                    "trace_id": "trace-safety",
                },
            }
        )
    )
    assert llm_response.ok is True

    ctx = _build_runtime_context(tmp_path, telemetryctl)
    tool_result = _h_exec_run({"command": "echo telemetry-safety"}, ctx)
    assert tool_result["status"] == "ok"

    compaction = CompactionService(telemetryctl=telemetryctl)
    compaction.set_telemetry_context(session_id="sess-safety", turn_id="turn-1")
    compaction.update(
        "sess-safety",
        [
            DeltaEvent(
                event_id="evt-1",
                event_type="turn.completed",
                text="Checkpoint the deploy plan safely.",
            )
        ],
    )
    assert compaction.maybe_checkpoint("sess-safety", reason="safety") is not None

    skill = Skill(_skill_config(tmp_path), telemetryctl=telemetryctl)
    skill.set_telemetry_context(session_id="sess-safety", turn_id="turn-1")
    skill_id, version_hash, warnings = skill.ingest_text(
        name="Restart Docker Services Safely",
        markdown=(
            "# Restart Docker Services Safely\n\n"
            "Use `docker ps`, check logs, then restart the service.\n"
        ),
    )
    assert warnings == []
    assert skill.match(
        intent_text="restart docker and inspect daemon logs",
        step_hint={"tool_id": "tool.shell", "risk": "medium"},
        agent_id="agent.ops",
        k=3,
    )
    assert skill.render_snippet(
        skill_id=skill_id,
        version_hash=version_hash,
        purpose="act",
        max_tokens=80,
    )[0]
    skill.close()

    store = InMemoryMemoryStore()
    store.put(
        MemoryRecord(
            id="mem-1",
            scope="session:sess-safety",
            type="fact",
            content="Aurora rollback plan",
            created_at="2026-03-28T00:00:00+00:00",
            updated_at="2026-03-28T00:00:00+00:00",
            meta={"bm25_score": 0.9},
        )
    )
    memory = MemoryService(store=store, telemetryctl=telemetryctl)
    memory.set_telemetry_context(session_id="sess-safety", turn_id="turn-1")
    assert memory.list(ListQueryOptions(scopes=["session:sess-safety"], limit=5))
    assert memory.search(
        SearchQueryOptions(
            query="aurora rollback",
            scopes=["session:sess-safety"],
            limit=5,
        )
    )
    assert memory.search_semantic(
        query="aurora rollback",
        scopes=["session:sess-safety"],
        limit=2,
    )

    retrieve = RetrieveCtl(_retrieve_config(tmp_path), telemetryctl=telemetryctl)
    retrieve.set_telemetry_context(session_id="sess-safety", turn_id="turn-1")
    retrieve.ingest_source(
        source_type="artifact",
        source_ref="artifact://sha256/" + ("b" * 64),
        text="Aurora deploy handbook with rollback checklist and owner handoff.",
        scope="session:sess-safety",
        tags=["ops"],
        title="Aurora handbook",
        unit_kind="chunk",
    )
    assert retrieve.retrieve(
        query="aurora rollback checklist",
        purpose="act",
        scope={"session_id": "sess-safety", "scope": "session:sess-safety"},
        k=3,
        strategy="contextual",
    )
    retrieve.close()

    manager = AgentRuntimeManager(
        turn_executor=lambda req, emit_chunk, cancel_event: TurnResponse(  # noqa: ARG005
            final_text=f"ok:{req.trace_id}",
            telemetry=TurnTelemetry(retries=1),
        ),
        telemetryctl=telemetryctl,
    )
    manager.start()
    try:
        handle = manager.submit_turn(
            TurnRequest(
                trace_id="trace-safety",
                agent_id="agent-runtime",
                session_id="sess-safety",
                input_text="hello",
            )
        )
        assert handle.result(timeout_s=2).final_text == "ok:trace-safety"
    finally:
        manager.shutdown()

    session_store = LocalSessionStore(tmp_path / "sessions")
    runner = BrainRunner(
        profile=_brain_profile(),
        session_api=session_store,
        context_api=LocalContextAdapter(session_store=session_store),
        tool_api=LocalToolAdapter(),
        a2a_api=LocalA2AAdapter(),
        memory_api=LocalMemoryAdapter(tmp_path / "memory"),
        policy_api=LocalPolicyAdapter(),
        telemetryctl=telemetryctl,
        options=RunnerOptions(metactl_enabled=False, failure_strategy="retry"),
    )
    state = runner._load_or_init_state("sess-safety")
    state.trace_id = "trace-safety"
    runner._build_context(
        state=state,
        purpose="decide",
        budget={"max_tokens": 100},
        hints={"user_input": 'tool echo {"msg":"hello"}'},
        logger=_DummyLogger(),
    )
    output = runner.run(
        session_id="sess-safety",
        user_input='tool echo {"msg":"hello"}',
        trace_id="trace-safety",
    )
    assert output.status == "done"

    adapter = SessctlAdapter(tmp_path / "sessctl.db", telemetryctl=telemetryctl)
    adapter.set_telemetry_context(session_id="sess-safety", turn_id="turn-1")
    adapter.append_turn("sess-safety", "user", "hello")
    adapter.append_turn("sess-safety", "assistant", "hi")
    adapter.get_slice("sess-safety", "act", {"max_turns": 5})

    if expect_warning:
        assert any(
            "telemetry emit failed" in record.message for record in caplog.records
        )
