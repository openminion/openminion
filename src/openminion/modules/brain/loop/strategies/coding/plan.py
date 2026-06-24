from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from openminion.modules.brain.schemas import Goal

CODING_PHASE_ORDER: tuple[str, ...] = ("explore", "plan", "implement", "verify")
CODING_PHASE_STATUS = Literal["pending", "active", "done", "failed"]
CODING_SUBTASK_STATUS = Literal["pending", "running", "done", "failed"]


class CodingSubtask(BaseModel):
    model_config = ConfigDict(extra="forbid")

    goal: str = Field(..., min_length=1)
    target_files: list[str] = Field(default_factory=list)
    success_criteria: str = ""
    status: CODING_SUBTASK_STATUS = "pending"


class CodingPhase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Literal["explore", "plan", "implement", "verify"]
    status: CODING_PHASE_STATUS = "pending"
    steps: list[str] = Field(default_factory=list)
    output: str = ""


class CodingPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    goal: str = Field(..., min_length=1)
    phases: list[CodingPhase] = Field(default_factory=list)
    current_phase: str = "implement"
    scratchpad: list[str] = Field(default_factory=list)
    completed_steps: list[str] = Field(default_factory=list)
    open_issues: list[str] = Field(default_factory=list)
    subtasks: list[CodingSubtask] = Field(default_factory=list)
    verifier_goal: Goal | None = None

    @model_validator(mode="after")
    def _validate_plan(self) -> "CodingPlan":
        if not self.phases:
            self.phases = [CodingPhase(name="implement", status="active")]
        ordered_names = [phase.name for phase in self.phases]
        ordered_indices = [CODING_PHASE_ORDER.index(name) for name in ordered_names]
        expected_indices = list(
            range(ordered_indices[0], ordered_indices[0] + len(ordered_indices))
        )
        if ordered_indices != expected_indices:
            raise ValueError(
                "phases must be an ordered contiguous span of "
                + " -> ".join(CODING_PHASE_ORDER)
            )
        if self.current_phase not in ordered_names:
            raise ValueError("current_phase must name one of the plan phases")

        active_seen = False
        normalized_phases: list[CodingPhase] = []
        for phase in self.phases:
            status = phase.status
            if phase.name == self.current_phase:
                status = "active"
                active_seen = True
            elif not active_seen and status == "pending":
                status = "done"
            elif active_seen and status == "done":
                status = "pending"
            normalized_phases.append(phase.model_copy(update={"status": status}))
        self.phases = normalized_phases
        return self

    @classmethod
    def fallback(cls, goal: str) -> "CodingPlan":
        return cls(
            goal=str(goal or "").strip() or "Complete the coding task.",
            phases=[CodingPhase(name="implement", status="active")],
            current_phase="implement",
        )

    def current_phase_entry(self) -> CodingPhase:
        for phase in self.phases:
            if phase.name == self.current_phase:
                return phase
        return self.phases[-1]

    def next_phase_name(self) -> str | None:
        current_index = [phase.name for phase in self.phases].index(self.current_phase)
        next_index = current_index + 1
        if next_index >= len(self.phases):
            return None
        return self.phases[next_index].name

    def advance_to_next_phase(self, *, output: str = "") -> bool:
        next_name = self.next_phase_name()
        current_phase = self.current_phase_entry()
        current_phase.output = str(output or "").strip()
        current_phase.status = "done"
        if next_name is None:
            return False
        self.current_phase = next_name
        for phase in self.phases:
            if phase.name == self.current_phase:
                phase.status = "active"
            elif phase.status != "done":
                phase.status = "pending"
        return True

    def record_open_issue(self, issue: str) -> None:
        text = str(issue or "").strip()
        if text:
            self.open_issues.append(text)

    def conflicting_subtask_pairs(self) -> list[tuple[int, int]]:
        pairs: list[tuple[int, int]] = []
        for left_index, left in enumerate(self.subtasks):
            left_paths = {Path(item) for item in left.target_files if str(item).strip()}
            for right_index in range(left_index + 1, len(self.subtasks)):
                right = self.subtasks[right_index]
                right_paths = {
                    Path(item) for item in right.target_files if str(item).strip()
                }
                if any(
                    left_path == right_path
                    or left_path in right_path.parents
                    or right_path in left_path.parents
                    for left_path in left_paths
                    for right_path in right_paths
                ):
                    pairs.append((left_index, right_index))
        return pairs

    def to_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


def coding_plan_from_payload(payload: Any, *, goal: str) -> CodingPlan:
    if isinstance(payload, CodingPlan):
        return payload
    if isinstance(payload, dict):
        try:
            return CodingPlan.model_validate(payload)
        except Exception:
            return CodingPlan.fallback(goal)
    return CodingPlan.fallback(goal)
