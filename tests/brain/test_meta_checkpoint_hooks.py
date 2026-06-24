from __future__ import annotations

from types import SimpleNamespace

from openminion.modules.brain.meta import evaluate_meta
from openminion.modules.brain.schemas import (
    BudgetCounters,
    Plan,
    ToolCommand,
    WorkingState,
)
from openminion.modules.brain.meta import MetaConfig as CanonicalMetaConfig
from openminion.modules.brain.meta import MetaRulesEngine as CanonicalMetaRulesEngine


class _CaptureLogger:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def emit(self, event_type: str, payload: dict, trace_id: str | None = None) -> None:
        _ = trace_id
        self.events.append((event_type, payload))


def _state() -> WorkingState:
    command = ToolCommand(
        title="Run command",
        tool_name="exec.run",
        args={"command": "echo hello"},
        success_criteria={"status": "success"},
        idempotency_key="checkpoint-idem",
        risk_level="med",
    )
    return WorkingState(
        session_id="checkpoint-session",
        agent_id="checkpoint-agent",
        trace_id="checkpoint-trace",
        budgets_remaining=BudgetCounters(
            ticks=10,
            tool_calls=5,
            a2a_calls=5,
            tokens=1000,
            time_ms=10000,
        ),
        plan=Plan(
            objective="checkpoint",
            steps=[command],
            stop_conditions=[],
            assumptions=[],
            risk_summary="",
            success_criteria={},
        ),
        cursor=0,
        status="active",
    )


def _runner() -> SimpleNamespace:
    return SimpleNamespace(
        _meta_overrides={},
        options=SimpleNamespace(
            metactl_enabled=True,
            metactl_config=CanonicalMetaConfig(),
        ),
        profile=SimpleNamespace(
            budgets=SimpleNamespace(
                max_ticks_per_user_turn=10,
                max_tool_calls=5,
                max_a2a_calls=5,
                max_total_llm_tokens=1000,
                max_elapsed_ms=10000,
            )
        ),
        meta_api=None,
        meta_engine=CanonicalMetaRulesEngine(CanonicalMetaConfig()),
    )


def test_evaluate_meta_emits_checkpoint_tags_for_all_hooks() -> None:
    hooks = [
        "after_interpret",
        "before_plan",
        "before_act",
        "after_observe",
        "before_respond",
    ]

    runner = _runner()
    for hook in hooks:
        logger = _CaptureLogger()
        result = evaluate_meta(
            runner,
            state=_state(),
            logger=logger,
            hook=hook,
            user_input="run test",
        )

        assert result is not None
        assert f"checkpoint:{hook}" in result.reasons
