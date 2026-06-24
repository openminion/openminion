from typing import Final, Literal

INTERVENTION_RECORDED_EVENT_TYPE = "brain.intervention_recorded"

AUDIT_EVENT_TYPE_PREFIX = "audit."

TYPED_TURN_INTENT_KIND_MISSION_RUNNER: Final[Literal["mission_runner"]] = (
    "mission_runner"
)
TYPED_TURN_INTENT_KIND_BENCHMARK_HARNESS: Final[Literal["benchmark_harness"]] = (
    "benchmark_harness"
)
TYPED_TURN_INTENT_KIND_SCRIPTED_CLI: Final[Literal["scripted_cli"]] = "scripted_cli"
TYPED_TURN_INTENT_KIND_TUI_TASK: Final[Literal["tui_task"]] = "tui_task"
TYPED_TURN_INTENT_KIND_FREEFORM_CHAT: Final[Literal["freeform_chat"]] = "freeform_chat"

__all__ = (
    "AUDIT_EVENT_TYPE_PREFIX",
    "INTERVENTION_RECORDED_EVENT_TYPE",
    "TYPED_TURN_INTENT_KIND_BENCHMARK_HARNESS",
    "TYPED_TURN_INTENT_KIND_FREEFORM_CHAT",
    "TYPED_TURN_INTENT_KIND_MISSION_RUNNER",
    "TYPED_TURN_INTENT_KIND_SCRIPTED_CLI",
    "TYPED_TURN_INTENT_KIND_TUI_TASK",
)
