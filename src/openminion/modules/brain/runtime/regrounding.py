from dataclasses import dataclass

from ..schemas.goals import Goal, SuccessCriterion


_REGROUNDING_BANNER = "[mrdd:regrounding]"


@dataclass(frozen=True)
class RegroundingPolicy:
    """Typed regrounding policy."""

    cadence_turns: int = 10
    enabled: bool = False
    inject_after_compaction: bool = True

    def __post_init__(self) -> None:  # pragma: no cover - simple guard
        if int(self.cadence_turns) < 1:
            raise ValueError(
                "RegroundingPolicy.cadence_turns must be >= 1; "
                f"got {self.cadence_turns!r}"
            )


@dataclass(frozen=True)
class RegroundingTrigger:
    """Typed regrounding trigger."""

    kind: str
    cadence_counter: int = 0
    just_compacted: bool = False
    forced: bool = False


@dataclass(frozen=True)
class RegroundingDecision:
    """Per-tick regrounding decision."""

    should_inject: bool
    trigger: RegroundingTrigger | None = None
    reason: str = ""


_REGROUNDING_TRIGGER_KINDS: frozenset[str] = frozenset(
    {"cadence", "post_compaction", "forced"}
)


def should_inject_regrounding(
    *,
    policy: RegroundingPolicy,
    cadence_counter: int,
    just_compacted: bool,
    forced: bool = False,
) -> RegroundingDecision:
    """Return whether to inject regrounding on this tick."""

    if not policy.enabled and not forced:
        return RegroundingDecision(
            should_inject=False,
            trigger=None,
            reason="regrounding_policy_disabled",
        )
    if forced:
        return RegroundingDecision(
            should_inject=True,
            trigger=RegroundingTrigger(kind="forced", forced=True),
            reason="forced_inject",
        )
    if policy.inject_after_compaction and just_compacted:
        return RegroundingDecision(
            should_inject=True,
            trigger=RegroundingTrigger(
                kind="post_compaction",
                cadence_counter=int(cadence_counter),
                just_compacted=True,
            ),
            reason="post_compaction_inject",
        )
    if int(cadence_counter) >= int(policy.cadence_turns):
        return RegroundingDecision(
            should_inject=True,
            trigger=RegroundingTrigger(
                kind="cadence",
                cadence_counter=int(cadence_counter),
            ),
            reason="cadence_threshold_reached",
        )
    return RegroundingDecision(
        should_inject=False,
        trigger=None,
        reason="cadence_threshold_not_reached",
    )


def build_regrounding_inject_text(goal: Goal) -> str:
    """Build the regrounding inject text."""

    lines: list[str] = []
    lines.append(_REGROUNDING_BANNER)
    lines.append(f"goal_id={goal.goal_id}")
    lines.append(f"description={goal.description}")
    if goal.success_criteria:
        lines.append("success_criteria:")
        for criterion in goal.success_criteria:
            lines.append(_format_criterion_line(criterion))
    return "\n".join(lines)


def _format_criterion_line(criterion: SuccessCriterion) -> str:
    return (
        f"- criterion_id={criterion.criterion_id} "
        f"structural_check={criterion.structural_check} "
        f"description={criterion.description}"
    )


@dataclass(frozen=True)
class RegroundingInject:
    """Typed regrounding inject payload for one tick."""

    text: str
    trigger: RegroundingTrigger
    goal_id: str


def build_regrounding_inject(
    *,
    goal: Goal,
    trigger: RegroundingTrigger,
) -> RegroundingInject:
    """Compose the inject payload for ``goal`` and ``trigger``."""

    if trigger.kind not in _REGROUNDING_TRIGGER_KINDS:
        raise ValueError(
            "RegroundingTrigger.kind must be in "
            f"{sorted(_REGROUNDING_TRIGGER_KINDS)}; got {trigger.kind!r}"
        )
    return RegroundingInject(
        text=build_regrounding_inject_text(goal),
        trigger=trigger,
        goal_id=goal.goal_id,
    )


@dataclass(frozen=True)
class RegroundingTickResult:
    """Typed result of evaluating regrounding for one tick."""

    inject: RegroundingInject | None
    decision: RegroundingDecision
    next_counter: int


def evaluate_regrounding_tick(
    *,
    goal: Goal,
    policy: RegroundingPolicy,
    cadence_counter: int,
    just_compacted: bool,
    forced: bool = False,
) -> RegroundingTickResult:
    """Compose policy, decision, and inject payload for one tick."""

    decision = should_inject_regrounding(
        policy=policy,
        cadence_counter=cadence_counter,
        just_compacted=just_compacted,
        forced=forced,
    )
    if decision.should_inject and decision.trigger is not None:
        inject = build_regrounding_inject(goal=goal, trigger=decision.trigger)
        return RegroundingTickResult(
            inject=inject,
            decision=decision,
            next_counter=0,
        )
    return RegroundingTickResult(
        inject=None,
        decision=decision,
        next_counter=int(cadence_counter) + 1,
    )


def compose_regrounding_section(inject: RegroundingInject) -> dict[str, str]:
    """Compose a structural context section for the inject."""

    return {
        "section": "regrounding",
        "body": inject.text,
        "goal_id": inject.goal_id,
        "trigger_kind": inject.trigger.kind,
    }


__all__ = [
    "RegroundingDecision",
    "RegroundingInject",
    "RegroundingPolicy",
    "RegroundingTickResult",
    "RegroundingTrigger",
    "build_regrounding_inject",
    "build_regrounding_inject_text",
    "compose_regrounding_section",
    "evaluate_regrounding_tick",
    "should_inject_regrounding",
]
