from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator

from openminion.modules.brain.diagnostics.events import CanonicalEventLogger
from openminion.modules.brain.runtime.verification.policy import (
    VerifierInvocation,
    VerifierResult,
    run_verifier,
)
from openminion.modules.brain.meta.schemas import VerificationMode
from openminion.modules.brain.schemas.goals import (
    Deliverable,
    FailureCondition,
    Goal,
    SuccessCriterion,
)
from openminion.modules.brain.schemas.missions import (
    MissionType,
    get_mission_verifier_expectation,
)
from openminion.modules.brain.schemas.state import (
    ActionResult,
    BudgetCounters,
    WorkingState,
)
from openminion.modules.brain.schemas.commands import ToolCommand
from openminion.modules.runtime.constants import (
    TYPED_TURN_INTENT_KIND_BENCHMARK_HARNESS,
    TYPED_TURN_INTENT_KIND_FREEFORM_CHAT,
    TYPED_TURN_INTENT_KIND_MISSION_RUNNER,
    TYPED_TURN_INTENT_KIND_SCRIPTED_CLI,
    TYPED_TURN_INTENT_KIND_TUI_TASK,
)
from openminion.modules.task.run import Run

TypedTurnIntentKind = Literal[
    "mission_runner",
    "benchmark_harness",
    "scripted_cli",
    "tui_task",
    "freeform_chat",
]


class _StructuredTypedTurnIntent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    goal_id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    mission_type: MissionType | None = None
    success_criteria: tuple[SuccessCriterion, ...] = Field(min_length=1)
    deliverables: tuple[Deliverable, ...] = Field(min_length=1)
    failure_conditions: tuple[FailureCondition, ...] = Field(default_factory=tuple)

    @field_validator("goal_id", "description", mode="before")
    @classmethod
    def _strip_text(cls, value: Any) -> str:
        return str(value or "").strip()


class _CanonicalSessionApiAdapter:
    """Bridge canonical-event logger calls onto SessionStore-like owners."""

    def __init__(self, session_api: Any) -> None:
        self._session_api = session_api

    def append_event(
        self,
        session_id: str,
        event_type: str,
        payload: dict[str, Any],
        **_: Any,
    ) -> str:
        record = self._session_api.append_event(
            session_id=session_id,
            event_type=event_type,
            payload=payload,
        )
        return getattr(record, "id", "") or event_type


class MissionRunnerTurnIntent(_StructuredTypedTurnIntent):
    kind: Literal["mission_runner"] = TYPED_TURN_INTENT_KIND_MISSION_RUNNER
    mission_id: str = Field(min_length=1)

    @field_validator("mission_id", mode="before")
    @classmethod
    def _strip_mission_id(cls, value: Any) -> str:
        return str(value or "").strip()


class BenchmarkHarnessTurnIntent(_StructuredTypedTurnIntent):
    kind: Literal["benchmark_harness"] = TYPED_TURN_INTENT_KIND_BENCHMARK_HARNESS
    corpus_task_id: str = Field(min_length=1)

    @field_validator("corpus_task_id", mode="before")
    @classmethod
    def _strip_task_id(cls, value: Any) -> str:
        return str(value or "").strip()


class ScriptedCliTurnIntent(_StructuredTypedTurnIntent):
    kind: Literal["scripted_cli"] = TYPED_TURN_INTENT_KIND_SCRIPTED_CLI
    command_name: str = Field(min_length=1)

    @field_validator("command_name", mode="before")
    @classmethod
    def _strip_command_name(cls, value: Any) -> str:
        return str(value or "").strip()


class TuiTaskTurnIntent(_StructuredTypedTurnIntent):
    kind: Literal["tui_task"] = TYPED_TURN_INTENT_KIND_TUI_TASK
    task_id: str = Field(min_length=1)

    @field_validator("task_id", mode="before")
    @classmethod
    def _strip_task_id(cls, value: Any) -> str:
        return str(value or "").strip()


class FreeformChatTurnIntent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["freeform_chat"] = TYPED_TURN_INTENT_KIND_FREEFORM_CHAT


TypedTurnIntent = Annotated[
    MissionRunnerTurnIntent
    | BenchmarkHarnessTurnIntent
    | ScriptedCliTurnIntent
    | TuiTaskTurnIntent
    | FreeformChatTurnIntent,
    Field(discriminator="kind"),
]

_TYPED_TURN_INTENT_ADAPTER: TypeAdapter[TypedTurnIntent] = TypeAdapter(TypedTurnIntent)


def parse_typed_turn_intent(raw: object) -> TypedTurnIntent:
    return _TYPED_TURN_INTENT_ADAPTER.validate_python(raw)


def resolve_typed_goal(turn_intent: TypedTurnIntent) -> Goal | None:
    if isinstance(turn_intent, FreeformChatTurnIntent):
        return None
    return Goal(
        goal_id=turn_intent.goal_id,
        description=turn_intent.description,
        success_criteria=list(turn_intent.success_criteria),
        deliverables=list(turn_intent.deliverables),
        failure_conditions=list(turn_intent.failure_conditions),
    )


def _empty_action_result(command_id: str) -> ActionResult:
    return ActionResult(
        command_id=command_id,
        status="failed",
        outputs={},
        artifact_refs=[],
        memory_refs=[],
    )


def _working_state(session_id: str, agent_id: str, run_id: str) -> WorkingState:
    return WorkingState(
        session_id=session_id,
        agent_id=agent_id,
        budgets_remaining=BudgetCounters(
            ticks=1,
            tool_calls=1,
            a2a_calls=0,
            tokens=1,
            time_ms=1,
        ),
        trace_id=f"gtgs-{run_id}",
    )


def _resolve_criterion_family(turn_intent: _StructuredTypedTurnIntent) -> str:
    if turn_intent.mission_type is None:
        return "structural"
    expectation = get_mission_verifier_expectation(turn_intent.mission_type)
    families = list(expectation.expected_verifier_families)
    return families[0] if families else "structural"


def _resolve_deliverable_family(
    turn_intent: _StructuredTypedTurnIntent,
    deliverable: Deliverable,
) -> str:
    if turn_intent.mission_type is None:
        return str(deliverable.verification_hint)
    expectation = get_mission_verifier_expectation(turn_intent.mission_type)
    families = list(expectation.expected_verifier_families)
    if deliverable.verification_hint in families:
        return str(deliverable.verification_hint)
    return str(families[0] if families else deliverable.verification_hint)


def build_fail_closed_terminal_resolution(
    *,
    turn_intent: TypedTurnIntent,
    run_id: str,
    session_id: str,
    agent_id: str,
    session_api: Any,
) -> tuple[Run, Goal, tuple[VerifierResult, ...], tuple[FailureCondition, ...]] | None:
    goal = resolve_typed_goal(turn_intent)
    if goal is None:
        return None
    if not isinstance(turn_intent, _StructuredTypedTurnIntent):
        return None
    if turn_intent.mission_type is not None:
        expectation = get_mission_verifier_expectation(turn_intent.mission_type)
        if not expectation.autonomous_completion_supported:
            return None

    logger = CanonicalEventLogger(
        session_api=_CanonicalSessionApiAdapter(session_api),
        session_id=session_id,
        agent_id=agent_id,
    )
    state = _working_state(session_id=session_id, agent_id=agent_id, run_id=run_id)
    verifier_results: list[VerifierResult] = []

    for criterion in goal.success_criteria:
        command = ToolCommand(
            kind="tool",
            title=f"verify-{criterion.criterion_id}",
            tool_name="gtgs-no-op",
            success_criteria={},
        )
        invocation = VerifierInvocation(
            family=_resolve_criterion_family(turn_intent),
            goal_id=goal.goal_id,
            run_id=run_id,
            command=command,
            action_result=_empty_action_result(command.command_id),
            criterion=criterion,
            mode=VerificationMode.rule_based,
        )
        verifier_results.append(run_verifier(invocation, state=state, logger=logger))

    for deliverable in goal.deliverables:
        command = ToolCommand(
            kind="tool",
            title=f"verify-{deliverable.deliverable_id}",
            tool_name="gtgs-no-op",
            success_criteria={},
        )
        invocation = VerifierInvocation(
            family=_resolve_deliverable_family(turn_intent, deliverable),
            goal_id=goal.goal_id,
            run_id=run_id,
            command=command,
            action_result=_empty_action_result(command.command_id),
            deliverable=deliverable,
            mode=VerificationMode.rule_based,
        )
        verifier_results.append(run_verifier(invocation, state=state, logger=logger))

    run = Run(
        run_id=run_id,
        session_id=session_id,
        goal_id=goal.goal_id,
        state="running",
    )
    return (run, goal, tuple(verifier_results), tuple())


__all__ = [
    "BenchmarkHarnessTurnIntent",
    "FreeformChatTurnIntent",
    "MissionRunnerTurnIntent",
    "ScriptedCliTurnIntent",
    "TuiTaskTurnIntent",
    "TypedTurnIntent",
    "TypedTurnIntentKind",
    "build_fail_closed_terminal_resolution",
    "parse_typed_turn_intent",
    "resolve_typed_goal",
]
