from __future__ import annotations

from typing import TYPE_CHECKING

from openminion.modules.skill.learning.runtime import (
    WorkflowObservationError,
    WorkflowObservationResult,
    list_runtime_workflow_observations,
    list_runtime_workflow_shapes,
    observe_runtime_workflow,
)
from openminion.modules.skill.learning.shapes import WorkflowShape

if TYPE_CHECKING:
    from openminion.modules.skill.config import SkillConfig
    from openminion.modules.skill.storage.base import SkillStore


class WorkflowObservationMixin:
    config: SkillConfig
    store: SkillStore

    @property
    def runtime_workflow_observation_enabled(self) -> bool:
        return bool(self.config.runtime_workflow_observation_enabled)

    def observe_workflow(
        self,
        *,
        agent_id: str,
        source_run_ref: str,
        intent_category: str,
        capability_category: str,
        tool_names: list[str],
    ) -> WorkflowObservationResult:
        if not self.runtime_workflow_observation_enabled:
            raise WorkflowObservationError("runtime workflow observation is disabled")
        return observe_runtime_workflow(
            self.store,
            agent_id=agent_id,
            source_run_ref=source_run_ref,
            intent_category=intent_category,
            capability_category=capability_category,
            tool_names=tool_names,
        )

    def list_workflow_observations(self, *, agent_id: str) -> list[dict[str, object]]:
        return [
            dict(observation)
            for observation in list_runtime_workflow_observations(
                self.store, agent_id=agent_id
            )
        ]

    def list_workflow_shapes(self, *, agent_id: str) -> list[WorkflowShape]:
        return list(list_runtime_workflow_shapes(self.store, agent_id=agent_id))
