from __future__ import annotations

import json
import logging
import sys
import tempfile
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from openminion.base.config import OpenMinionConfig
from openminion.base.generated_paths import resolve_generated_root
from openminion.base.types import Message
from openminion.modules.brain.constants import (
    BRAIN_ACTION_STATUS_FAILED,
)
from openminion.modules.brain.execution.loop_contracts import (
    ExecutionContext,
    ExecutionResult,
)
from openminion.modules.brain.execution.targets.delegated.handler import DelegateMode
from openminion.modules.brain.schemas import ActionResult, BudgetCounters, WorkingState
from openminion.modules.context.knowledge import (
    CAPABILITY_CITATIONS,
    CAPABILITY_PROVENANCE,
    CAPABILITY_QUERY,
    GraphQueryRequest,
    KnowledgeGraphRegistry,
    build_knowledge_graph_service,
)
from openminion.modules.context.knowledge.adapters.pragmagraph import (
    PragmaGraphKnowledgeGraphSource,
)
from openminion.modules.memory.config import from_base_config
from openminion.modules.memory.service import MemoryService
from openminion.modules.memory.storage.base import ListQueryOptions
from openminion.modules.memory.storage.sqlite.store import SQLiteMemoryStore
from openminion.services.agent.memory.gateway_adapter import MemoryServiceGatewayAdapter
from openminion.services.constants import MEMORY_CAPSULE_STRATEGY_DYNAMIC_TURN
from openminion.services.gateway.context import build_turn_context
from tests.helpers.real_a2a_delegate_harness import (
    RealA2ADelegateHarness,
    TargetExecutionRecord,
)

DEFAULT_FLAGSHIP_INPUT = (
    "Use my preferred answer style to explain RuntimeGraph, cite the source, "
    "and ask repo-analyst to inspect the fixture."
)
DEFAULT_MEMORY_TEXT = "Keep answers terse and cite file paths."
DEFAULT_DELEGATE_TARGET = "repo-analyst"
DEFAULT_DELEGATE_GOAL = (
    "Inspect the fixture repo and report the Python file count plus README presence."
)

_LOGGER = logging.getLogger("tests.helpers.flagship_differentiation_proof")


@dataclass(slots=True)
class FlagshipProofResult:
    artifact: dict[str, Any]
    artifact_path: Path
    final_answer: str


_MODEL_FACING_DISCLOSURE = (
    "Deterministic integration proof only: the delegation decision and final answer "
    "are fixture-driven Python orchestration, not model-produced outputs. This proof "
    "demonstrates that real memory, DelegateMode/A2A, and PragmaGraph surfaces compose "
    "correctly and emit provenance-separated evidence. A recorded or live model turn "
    "remains the follow-on required for a full model-facing claim."
)


@dataclass
class _Runner:
    agent_registry: dict[str, dict[str, str]]
    a2a_api: Any
    task_manager: Any | None = None


@dataclass
class _DelegateServices:
    runner: _Runner
    harness: RealA2ADelegateHarness
    command_calls: list[Any]
    statuses: list[dict[str, Any]]

    def save_state(self, *, state: WorkingState) -> None:
        del state

    def emit_phase_status(self, *, state: WorkingState, **kwargs: Any) -> None:
        del state
        self.statuses.append(dict(kwargs))

    def respond_with_meta(
        self,
        *,
        state: WorkingState,
        logger: Any,
        message: str,
        status: str,
        action_result: ActionResult | None = None,
    ) -> Any:
        del logger
        state.status = status
        if action_result is not None:
            state.last_result = action_result
        return SimpleNamespace(
            session_id=state.session_id,
            status=status,
            message=message,
            working_state=state,
            action_result=action_result,
        )

    def direct_response(self, *, user_input: str, decision: Any) -> str:
        del user_input, decision
        return ""

    def plan(self, *, state: WorkingState, user_input: str, logger: Any, decision=None):
        del state, user_input, logger, decision
        raise AssertionError("flagship proof should not call ctx.plan()")

    def approve_command(self, *, state: WorkingState, command: Any, logger: Any) -> Any:
        del state, logger
        return command

    def act_command(self, *, state: WorkingState, command: Any, logger: Any):
        del logger
        self.command_calls.append(command)
        return self.harness.action_from_command(
            command=command,
            session_id=state.session_id,
            trace_id=str(state.trace_id or ""),
        )

    def transition_task(
        self,
        *,
        task_id: str,
        to_state: str,
        failure_reason: str | None = None,
    ):
        del task_id, to_state, failure_reason
        raise AssertionError("flagship proof does not use task transitions")

    def assess_plan_feasibility(
        self, *, state: WorkingState, user_input: str, logger: Any
    ):
        del state, user_input, logger
        return None

    def evaluate_meta(self, **kwargs: Any):
        del kwargs
        return None

    def apply_meta_directive(self, **kwargs: Any) -> None:
        del kwargs

    def meta_override_response(self, **kwargs: Any):
        del kwargs
        return None

    def meta_tool_restriction_reason(self, *, command: Any, directive: Any):
        del command, directive
        return None

    def command_has_side_effects(self, *, command: Any) -> bool:
        del command
        return True

    def resolve_verification_mode(self, *, current: Any, candidate: Any) -> Any:
        return candidate if candidate is not None else current

    def verify(
        self,
        *,
        state: WorkingState,
        command: Any,
        action_result: ActionResult,
        mode: str,
        logger: Any,
    ) -> bool:
        del state, command, action_result, mode, logger
        return True

    def improve(self, *, state: WorkingState, report: Any, logger: Any) -> None:
        del state, report, logger

    def compact(self, *, state: WorkingState, logger: Any, content: str = "") -> None:
        del state, logger, content

    def evaluate_turn_closure(self, **kwargs: Any):
        del kwargs
        return None

    def apply_closure_judgment(self, *, state: WorkingState, judgment: Any) -> str:
        del state, judgment
        return "close"

    def extract_success_memories(self, **kwargs: Any) -> list[Any]:
        del kwargs
        return []


def _ensure_pragmagraph_src() -> Path:
    workspace_root = Path(__file__).resolve().parents[3]
    pragmagraph_src = workspace_root / "pragmagraph" / "src"
    if str(pragmagraph_src) not in sys.path:
        sys.path.insert(0, str(pragmagraph_src))
    return pragmagraph_src


def _fixture_root() -> Path:
    return (
        Path(__file__).resolve().parents[3] / "pragmagraph" / "fixtures" / "tiny_repo"
    )


def _default_output_path() -> Path:
    return (
        resolve_generated_root(home_root=Path(__file__).resolve().parents[3])
        / "flagship-differentiation"
        / "flagship-proof.json"
    ).resolve(strict=False)


def _memory_config():
    cfg = from_base_config(
        base_config=OpenMinionConfig(),
        home_root=Path("/tmp/openminion-home"),
        data_root=Path("/tmp/openminion-data"),
    )
    return replace(
        cfg,
        candidate_learning=replace(
            cfg.candidate_learning,
            auto_extract_enabled=False,
            auto_extract_notify=True,
        ),
    )


def _build_pragmagraph_service(snapshot_path: Path):
    registry = KnowledgeGraphRegistry()
    registry.register("pragmagraph", PragmaGraphKnowledgeGraphSource)
    return build_knowledge_graph_service(
        {
            "provider": {
                "active": ["repo_pragmas"],
                "providers": {
                    "repo_pragmas": {
                        "provider": "pragmagraph",
                        "required_capabilities": [
                            CAPABILITY_QUERY,
                            CAPABILITY_CITATIONS,
                            CAPABILITY_PROVENANCE,
                        ],
                        "options": {"snapshot_path": str(snapshot_path)},
                    }
                },
            }
        },
        registry=registry,
    )


def _seed_memory(
    *, memory_adapter: MemoryServiceGatewayAdapter, session_id: str
) -> None:
    memory_adapter.record_turn(
        session_id=session_id,
        run_id="flagship-memory-run-1",
        request_id="flagship-memory-req-1",
        channel="console",
        target="chat",
        user_message=f"remember: preference memory: {DEFAULT_MEMORY_TEXT}",
        assistant_message="Captured.",
    )


def _memory_context_and_record(
    *,
    memory_service: MemoryService,
    memory_adapter: MemoryServiceGatewayAdapter,
    agent_id: str,
    session_id: str,
    user_input: str,
) -> tuple[str, dict[str, Any], Any]:
    context, meta = memory_adapter.build_context_with_metadata(
        session_id=session_id,
        user_message=user_input,
    )
    records = memory_service.list(
        ListQueryOptions(
            scopes=[f"agent:{agent_id}"],
            types=["user_preference"],
            limit=10,
        )
    )
    if not records:
        raise AssertionError("flagship proof expected one stored preference record")
    return context, meta, records[0]


def _build_integrated_context(
    *,
    memory_adapter: MemoryServiceGatewayAdapter,
    knowledge_graphs: Any,
    user_input: str,
) -> tuple[Any, list[dict[str, Any]]]:
    events: list[dict[str, Any]] = []

    def _emit_memory_event(**kwargs: Any) -> None:
        events.append(dict(kwargs))

    context = build_turn_context(
        history=[
            Message(
                channel="console",
                target="local-user",
                body=user_input,
                metadata={"role": "user"},
            )
        ],
        agent_id="flagship-main",
        agent_memory=memory_adapter,
        logger=_LOGGER,
        emit_memory_event=_emit_memory_event,
        session_id="flagship-session",
        run_id="flagship-run",
        request_id="flagship-request",
        channel="console",
        target="local-user",
        user_message=user_input,
        conversation_id="flagship-conversation",
        thread_id="flagship-thread",
        attach_id="flagship-attach",
        memory_capsule_strategy=MEMORY_CAPSULE_STRATEGY_DYNAMIC_TURN,
        memory_capsule_cache={},
        memory_dynamic_retrieval_enabled=True,
        knowledge_graphs=knowledge_graphs,
    )
    return context, events


def _register_delegate_target(
    *,
    harness: RealA2ADelegateHarness,
    fixture_root: Path,
    target_agent_id: str,
) -> None:
    normalized_target = str(target_agent_id).strip()
    fixture_root = fixture_root.resolve(strict=False)

    def _handler(envelope: Any) -> dict[str, Any]:
        params = dict(getattr(envelope, "params", {}) or {})
        goal = str(params.get("goal", "") or "").strip()
        python_files = sorted(
            str(path.relative_to(fixture_root)) for path in fixture_root.rglob("*.py")
        )
        markdown_files = sorted(
            str(path.relative_to(fixture_root)) for path in fixture_root.rglob("*.md")
        )
        record = TargetExecutionRecord(
            target_agent_id=normalized_target,
            from_agent=str(getattr(envelope, "from_agent", "") or ""),
            method=str(getattr(envelope, "method", "") or ""),
            trace_id=str(getattr(envelope, "trace_id", "") or ""),
            params=params,
        )
        harness.records.append(record)
        return {
            "summary": (
                f"{normalized_target} inspected the fixture repo and found "
                f"{len(python_files)} Python file(s)."
            ),
            "target_agent_id": normalized_target,
            "received_goal": goal,
            "python_file_count": len(python_files),
            "markdown_file_count": len(markdown_files),
            "python_files": python_files,
            "markdown_files": markdown_files,
            "lineage": {
                "from_agent": record.from_agent,
                "target_agent_id": normalized_target,
                "trace_id": record.trace_id,
            },
        }

    harness.adapter.register_agent(
        normalized_target,
        ["delegate", "run", "task"],
        _handler,
        tags=["test", "profile-backed", "flagship-proof"],
    )


def _delegate_state(*, session_id: str, trace_id: str) -> WorkingState:
    return WorkingState(
        session_id=session_id,
        agent_id="flagship-main",
        goal="Produce a flagship proof",
        budgets_remaining=BudgetCounters(
            ticks=8,
            tool_calls=4,
            a2a_calls=3,
            tokens=8000,
            time_ms=120000,
        ),
        trace_id=trace_id,
    )


def _delegate_context(
    *,
    harness: RealA2ADelegateHarness,
    target_agent_id: str,
    goal: str,
    session_id: str,
    trace_id: str,
) -> tuple[ExecutionContext, _DelegateServices]:
    registry = {
        item["agent_id"]: {"state": item.get("status", "online")}
        for item in harness.list_agents()
    }
    services = _DelegateServices(
        runner=_Runner(agent_registry=registry, a2a_api=harness.adapter),
        harness=harness,
        command_calls=[],
        statuses=[],
    )
    decision = SimpleNamespace(
        mode="delegate",
        confidence=0.98,
        reason_code="flagship_delegate_proof",
        target_agent_id=target_agent_id,
        target_capability=None,
        goal=goal,
        constraints="Return structured fixture facts only.",
        synthesize_result=False,
        timeout_ms=2500,
        sub_intents=[],
        rationale="Use the configured-agent delegation path for proof.",
        question=None,
        answer=None,
    )
    ctx = ExecutionContext(
        state=_delegate_state(session_id=session_id, trace_id=trace_id),
        decision=decision,
        user_input="delegate to the configured repo specialist",
        logger=SimpleNamespace(emit=lambda *args, **kwargs: None),
        options=SimpleNamespace(decompose_cancel_requested=False),
        llm_adapter=None,
        command_executor=SimpleNamespace(),
        _services=services,
    )
    return ctx, services


def _run_delegate_proof(
    *, fixture_root: Path
) -> tuple[ExecutionResult, dict[str, Any]]:
    with tempfile.TemporaryDirectory(prefix="flagship-delegate-") as tmp:
        home_root = Path(tmp) / "home"
        home_root.mkdir(parents=True, exist_ok=True)
        harness = RealA2ADelegateHarness(
            home_root=home_root, caller_agent_id="flagship-main"
        )
        try:
            _register_delegate_target(
                harness=harness,
                fixture_root=fixture_root,
                target_agent_id=DEFAULT_DELEGATE_TARGET,
            )
            ctx, services = _delegate_context(
                harness=harness,
                target_agent_id=DEFAULT_DELEGATE_TARGET,
                goal=DEFAULT_DELEGATE_GOAL,
                session_id="flagship-delegate-session",
                trace_id="flagship-delegate-trace",
            )
            result = DelegateMode().execute(ctx)
            traces = harness.trace_events("flagship-delegate-trace")
            payload = {
                "surface": "service-level-brain-delegate-mode",
                "target_agent_id": DEFAULT_DELEGATE_TARGET,
                "goal": DEFAULT_DELEGATE_GOAL,
                "result_status": result.status,
                "result_message": str(result.message or ""),
                "action_result": _action_result_to_dict(result.action_result),
                "command_calls": [
                    str(getattr(item, "title", "") or "")
                    for item in services.command_calls
                ],
                "records": [asdict(item) for item in harness.records],
                "trace_events": traces,
            }
            return result, payload
        finally:
            harness.close()


def _action_result_to_dict(action_result: ActionResult | None) -> dict[str, Any]:
    if action_result is None:
        return {}
    payload: dict[str, Any] = {
        "command_id": action_result.command_id,
        "status": action_result.status,
        "summary": action_result.summary,
        "outputs": dict(action_result.outputs or {}),
    }
    if action_result.error is not None:
        payload["error"] = {
            "code": action_result.error.code,
            "message": action_result.error.message,
            "details": dict(action_result.error.details or {}),
        }
    return payload


def _render_final_answer(
    *,
    artifact_path: Path,
    memory_record: Any,
    delegate_payload: dict[str, Any],
    graph_payload: dict[str, Any],
) -> str:
    outputs = dict(delegate_payload.get("action_result", {}).get("outputs", {}) or {})
    graph_source = graph_payload.get("source_ref", {})
    graph_path = graph_source.get("path", "")
    graph_line = graph_source.get("line")
    line_suffix = f":{graph_line}" if graph_line is not None else ""
    memory_text = str(memory_record.content or "").strip()
    if memory_text.lower().startswith("preference memory:"):
        memory_text = memory_text.split(":", 1)[1].strip()
    return (
        f"Preference remembered: {memory_text} "
        f"Delegated fixture inspection to {delegate_payload['target_agent_id']}, "
        f"which found {outputs.get('python_file_count', 0)} Python file(s). "
        f"PragmaGraph cited {graph_path}{line_suffix} for RuntimeGraph. "
        f"Evidence packet: {artifact_path}."
    )


def run_flagship_differentiation_proof(
    *,
    user_input: str = DEFAULT_FLAGSHIP_INPUT,
    output_path: Path | None = None,
) -> FlagshipProofResult:
    _ensure_pragmagraph_src()
    from pragmagraph.adapters import index_path
    from pragmagraph.storage import save_snapshot

    fixture_root = _fixture_root()
    output_path = (output_path or _default_output_path()).resolve(strict=False)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="flagship-proof-") as tmp:
        tmp_root = Path(tmp)
        snapshot_path = tmp_root / "flagship-pragmagraph-snapshot.json"
        save_snapshot(
            index_path(fixture_root, namespace="flagship-fixture"), snapshot_path
        )
        knowledge_graphs = _build_pragmagraph_service(snapshot_path)

        memory_store = SQLiteMemoryStore(tmp_root / "flagship-memory.db")
        memory_service = MemoryService(store=memory_store)
        memory_adapter = MemoryServiceGatewayAdapter(
            memory_service,
            agent_id="flagship-main",
            project_id="flagship-project",
            memory_config=_memory_config(),
            capsule_max_chars=2400,
        )
        session_id = "flagship-memory-session"
        _seed_memory(memory_adapter=memory_adapter, session_id=session_id)
        memory_context, memory_meta, memory_record = _memory_context_and_record(
            memory_service=memory_service,
            memory_adapter=memory_adapter,
            agent_id="flagship-main",
            session_id=session_id,
            user_input=user_input,
        )

        graph_results = knowledge_graphs.query(GraphQueryRequest(query="RuntimeGraph"))
        if not graph_results or not graph_results[0].items:
            raise AssertionError("flagship proof expected PragmaGraph query results")
        graph_result = graph_results[0]
        graph_item = graph_result.items[0]

        turn_context, context_events = _build_integrated_context(
            memory_adapter=memory_adapter,
            knowledge_graphs=knowledge_graphs,
            user_input=user_input,
        )
        delegate_result, delegate_payload = _run_delegate_proof(
            fixture_root=fixture_root
        )
        if (
            delegate_payload.get("action_result", {}).get("status")
            == BRAIN_ACTION_STATUS_FAILED
        ):
            raise AssertionError(f"delegate proof failed: {delegate_result.message}")

        graph_payload = {
            "provider": graph_result.provider,
            "layer": graph_result.layer,
            "tags": list(graph_result.tags),
            "query": "RuntimeGraph",
            "source_graph_id": graph_item.source_graph_id,
            "node_or_edge_id": graph_item.node_or_edge_id,
            "source_ref": {
                "path": graph_item.source_ref.path,
                "line": graph_item.source_ref.line,
            },
            "snippet": graph_item.snippet,
            "omitted_count": len(graph_result.omitted),
        }

        final_answer = _render_final_answer(
            artifact_path=output_path,
            memory_record=memory_record,
            delegate_payload=delegate_payload,
            graph_payload=graph_payload,
        )

        artifact = {
            "proof_lane": "openminion-flagship-differentiation-proof",
            "proof_mode": "deterministic-integration",
            "scenario": {
                "user_input": user_input,
                "memory_text": DEFAULT_MEMORY_TEXT,
                "delegate_target": DEFAULT_DELEGATE_TARGET,
                "delegate_goal": DEFAULT_DELEGATE_GOAL,
                "fixture_root": str(fixture_root),
            },
            "claim_calibration": {
                "model_facing": False,
                "disclosure": _MODEL_FACING_DISCLOSURE,
                "follow_on": (
                    "Add one recorded or live model-in-the-loop turn and replay it "
                    "through the standalone openminion_eval package before claiming "
                    "a model-facing proof."
                ),
            },
            "memory": {
                "scope": memory_record.scope,
                "record_type": memory_record.type,
                "title": memory_record.title,
                "content": memory_record.content,
                "provenance": {
                    "owner": "sophiagraph-second-brain",
                    "service": "MemoryServiceGatewayAdapter",
                },
                "retrieval_context": memory_context,
                "retrieval_meta": dict(memory_meta or {}),
            },
            "delegation": delegate_payload,
            "provider": {
                **graph_payload,
                "provenance": {
                    "owner": "pragmagraph-third-brain",
                    "provider_label": graph_result.provider,
                },
            },
            "integrated_context": {
                "body": turn_context.history[-1].body,
                "history": [
                    {
                        "body": message.body,
                        "metadata": dict(message.metadata or {}),
                    }
                    for message in turn_context.history
                ],
                "metadata": dict(turn_context.history[-1].metadata or {}),
                "events": context_events,
            },
            "boundary_assertions": [
                "Sophiagraph durable memory stores operator preference/provenance.",
                "PragmaGraph stores observed repo facts and source refs.",
                "The final answer may cite both without merging their ownership.",
            ],
            "final_answer": final_answer,
            "artifact_path": str(output_path),
        }
        output_path.write_text(
            json.dumps(artifact, indent=2, sort_keys=True), encoding="utf-8"
        )
        return FlagshipProofResult(
            artifact=artifact,
            artifact_path=output_path,
            final_answer=final_answer,
        )
