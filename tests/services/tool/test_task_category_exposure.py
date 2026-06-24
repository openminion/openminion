from __future__ import annotations

import unittest
from dataclasses import dataclass
from typing import List, Optional
from unittest.mock import MagicMock

from openminion.base.config.tool_selection import ToolSelectionConfig
from openminion.modules.llm.providers.base import ProviderToolSpec
from openminion.modules.brain.loop.adaptive import ACT_ADAPTIVE_ALLOWED_TOOLS
from openminion.modules.tool.contracts.model_ids import (
    MODEL_TASK_CANCEL,
    MODEL_TASK_CONSOLIDATE_MEMORY,
    MODEL_TASK_LIST,
    MODEL_TASK_PAUSE,
    MODEL_TASK_RESUME,
    MODEL_TASK_SCHEDULE,
    MODEL_TASK_SHOW,
    MODEL_TASK_WATCH,
    MODEL_WEATHER,
)
from openminion.services.tool.selection import (
    SchemaExposure,
    SelectionMode,
    ToolSelectionService,
    _PREFERRED_MODEL_TOOLS_BY_CATEGORY,
)


@dataclass
class _FakeCategoryEntry:
    primary_category: str
    secondary_categories: List[str]


class _FakeRegistry:
    def __init__(
        self,
        specs: List[ProviderToolSpec],
        category_entries: Optional[dict[str, _FakeCategoryEntry]] = None,
    ) -> None:
        self._specs = list(specs)
        self._cats = dict(category_entries or {})

    def provider_spec_for_name(self, name: str) -> Optional[ProviderToolSpec]:
        for spec in self._specs:
            if spec.name == name:
                return spec
        return None

    def category_for_tool(self, name: str) -> _FakeCategoryEntry:
        return self._cats.get(name, _FakeCategoryEntry("", []))

    def _binding_manager(self):  # pragma: no cover
        return None

    def tools_by_category(self, category: str) -> list[str]:
        matches: list[str] = []
        for tool_name, entry in self._cats.items():
            if entry.primary_category == category:
                matches.append(tool_name)
            elif category in entry.secondary_categories:
                matches.append(tool_name)
        return matches


def _spec(name: str, description: str = "") -> ProviderToolSpec:
    return ProviderToolSpec(
        name=name,
        description=description,
        parameters={"type": "object", "properties": {}, "required": []},
    )


def _make_service(
    specs: List[ProviderToolSpec],
    *,
    mode: SelectionMode = SelectionMode.TYPED,
    max_tools_per_turn: int = 10,
    tool_prompt_token_budget: int = 10_000,
    category_entries: Optional[dict[str, _FakeCategoryEntry]] = None,
    bindings: Optional[dict[str, str]] = None,
) -> ToolSelectionService:
    config = ToolSelectionConfig(
        mode=mode.value,
        max_tools_per_turn=max_tools_per_turn,
        tool_prompt_token_budget=tool_prompt_token_budget,
        bindings=dict(bindings or {}),
    )
    registry = _FakeRegistry(specs, category_entries=category_entries)
    service = ToolSelectionService.__new__(ToolSelectionService)
    service._config = config
    service._registry = registry
    service._mode = mode
    service._schema_exposure = SchemaExposure(config.schema_exposure)
    service._registry_specs = MagicMock(return_value=list(specs))  # type: ignore[method-assign]
    return service


class TaskCategoryPreferenceTableTests(unittest.TestCase):
    def test_task_schedule_category_lists_model_task_schedule(self) -> None:
        self.assertIn("task.schedule", _PREFERRED_MODEL_TOOLS_BY_CATEGORY)
        self.assertEqual(
            _PREFERRED_MODEL_TOOLS_BY_CATEGORY["task.schedule"],
            [MODEL_TASK_SCHEDULE],
        )

    def test_task_list_category_lists_model_task_list(self) -> None:
        self.assertIn("task.list", _PREFERRED_MODEL_TOOLS_BY_CATEGORY)
        self.assertEqual(
            _PREFERRED_MODEL_TOOLS_BY_CATEGORY["task.list"],
            [MODEL_TASK_LIST],
        )

    def test_task_cancel_category_lists_model_task_cancel(self) -> None:
        self.assertIn("task.cancel", _PREFERRED_MODEL_TOOLS_BY_CATEGORY)
        self.assertEqual(
            _PREFERRED_MODEL_TOOLS_BY_CATEGORY["task.cancel"],
            [MODEL_TASK_CANCEL],
        )

    def test_task_watch_category_lists_model_task_watch(self) -> None:
        self.assertIn("task.watch", _PREFERRED_MODEL_TOOLS_BY_CATEGORY)
        self.assertEqual(
            _PREFERRED_MODEL_TOOLS_BY_CATEGORY["task.watch"],
            [MODEL_TASK_WATCH],
        )

    def test_task_pause_category_lists_model_task_pause(self) -> None:
        self.assertIn("task.pause", _PREFERRED_MODEL_TOOLS_BY_CATEGORY)
        self.assertEqual(
            _PREFERRED_MODEL_TOOLS_BY_CATEGORY["task.pause"],
            [MODEL_TASK_PAUSE],
        )

    def test_task_resume_category_lists_model_task_resume(self) -> None:
        self.assertIn("task.resume", _PREFERRED_MODEL_TOOLS_BY_CATEGORY)
        self.assertEqual(
            _PREFERRED_MODEL_TOOLS_BY_CATEGORY["task.resume"],
            [MODEL_TASK_RESUME],
        )

    def test_task_show_category_lists_model_task_show(self) -> None:
        self.assertIn("task.show", _PREFERRED_MODEL_TOOLS_BY_CATEGORY)
        self.assertEqual(
            _PREFERRED_MODEL_TOOLS_BY_CATEGORY["task.show"],
            [MODEL_TASK_SHOW],
        )

    def test_task_consolidate_memory_category_lists_consolidate_memory_tool(
        self,
    ) -> None:
        self.assertIn("task.consolidate_memory", _PREFERRED_MODEL_TOOLS_BY_CATEGORY)
        self.assertEqual(
            _PREFERRED_MODEL_TOOLS_BY_CATEGORY["task.consolidate_memory"],
            [MODEL_TASK_CONSOLIDATE_MEMORY],
        )


class TaskCategoryDeterministicSelectionTests(unittest.TestCase):
    def test_task_schedule_intent_category_selects_task_schedule_tool(
        self,
    ) -> None:
        service = _make_service(
            [_spec(MODEL_TASK_SCHEDULE), _spec(MODEL_WEATHER)],
            mode=SelectionMode.TYPED,
            bindings={"task.schedule": MODEL_TASK_SCHEDULE},
            category_entries={
                MODEL_TASK_SCHEDULE: _FakeCategoryEntry("task.schedule", []),
                MODEL_WEATHER: _FakeCategoryEntry("weather", []),
            },
        )
        result = service.select_tools(
            query="schedule a weekly cleanup",
            intent_categories=["task.schedule"],
        )
        self.assertEqual(result.mode, "deterministic")
        self.assertIn(MODEL_TASK_SCHEDULE, result.shortlist)
        # Negative path: weather tool must NOT slip into the task shortlist.
        self.assertNotIn(MODEL_WEATHER, result.shortlist)

    def test_task_list_forced_category_selects_task_list_tool(self) -> None:
        service = _make_service(
            [_spec(MODEL_TASK_LIST), _spec(MODEL_TASK_SCHEDULE)],
            mode=SelectionMode.TYPED,
            bindings={"task.list": MODEL_TASK_LIST},
            category_entries={
                MODEL_TASK_LIST: _FakeCategoryEntry("task.list", []),
                MODEL_TASK_SCHEDULE: _FakeCategoryEntry("task.schedule", []),
            },
        )
        result = service.select_tools(
            query="show pending tasks",
            forced_category="task.list",
        )
        self.assertEqual(result.mode, "deterministic")
        self.assertIn(MODEL_TASK_LIST, result.shortlist)
        # Negative path: sibling task.schedule must not slip in via category leak.
        self.assertNotIn(MODEL_TASK_SCHEDULE, result.shortlist)

    def test_task_cancel_intent_category_selects_task_cancel_tool(self) -> None:
        service = _make_service(
            [_spec(MODEL_TASK_CANCEL), _spec(MODEL_WEATHER)],
            mode=SelectionMode.TYPED,
            bindings={"task.cancel": MODEL_TASK_CANCEL},
            category_entries={
                MODEL_TASK_CANCEL: _FakeCategoryEntry("task.cancel", []),
                MODEL_WEATHER: _FakeCategoryEntry("weather", []),
            },
        )
        result = service.select_tools(
            query="stop the scheduled task",
            intent_categories=["task.cancel"],
        )
        self.assertEqual(result.mode, "deterministic")
        self.assertIn(MODEL_TASK_CANCEL, result.shortlist)

    def test_task_show_forced_category_selects_task_show_tool(self) -> None:
        service = _make_service(
            [_spec(MODEL_TASK_SHOW), _spec(MODEL_TASK_LIST)],
            mode=SelectionMode.TYPED,
            bindings={"task.show": MODEL_TASK_SHOW},
            category_entries={
                MODEL_TASK_SHOW: _FakeCategoryEntry("task.show", []),
                MODEL_TASK_LIST: _FakeCategoryEntry("task.list", []),
            },
        )
        result = service.select_tools(
            query="show one scheduled task",
            forced_category="task.show",
        )
        self.assertEqual(result.mode, "deterministic")
        self.assertIn(MODEL_TASK_SHOW, result.shortlist)
        self.assertNotIn(MODEL_TASK_LIST, result.shortlist)


class TaskAdaptiveAllowedToolTests(unittest.TestCase):
    def test_general_adaptive_allowed_tools_include_task_crud(self) -> None:
        self.assertIn(MODEL_TASK_SCHEDULE, ACT_ADAPTIVE_ALLOWED_TOOLS)
        self.assertIn(MODEL_TASK_LIST, ACT_ADAPTIVE_ALLOWED_TOOLS)
        self.assertIn(MODEL_TASK_CANCEL, ACT_ADAPTIVE_ALLOWED_TOOLS)
        self.assertIn(MODEL_TASK_PAUSE, ACT_ADAPTIVE_ALLOWED_TOOLS)
        self.assertIn(MODEL_TASK_RESUME, ACT_ADAPTIVE_ALLOWED_TOOLS)
        self.assertIn(MODEL_TASK_SHOW, ACT_ADAPTIVE_ALLOWED_TOOLS)


class UnrelatedCategoryDoesNotPullTaskToolsTests(unittest.TestCase):
    def test_weather_category_does_not_surface_task_tools(self) -> None:
        service = _make_service(
            [
                _spec(MODEL_WEATHER),
                _spec(MODEL_TASK_SCHEDULE),
                _spec(MODEL_TASK_LIST),
                _spec(MODEL_TASK_CANCEL),
            ],
            mode=SelectionMode.TYPED,
            bindings={"weather": MODEL_WEATHER},
            category_entries={
                MODEL_WEATHER: _FakeCategoryEntry("weather", []),
                MODEL_TASK_SCHEDULE: _FakeCategoryEntry("task.schedule", []),
                MODEL_TASK_LIST: _FakeCategoryEntry("task.list", []),
                MODEL_TASK_CANCEL: _FakeCategoryEntry("task.cancel", []),
            },
        )
        result = service.select_tools(
            query="temperature in tokyo",
            forced_category="weather",
        )
        self.assertEqual(result.mode, "deterministic")
        self.assertIn(MODEL_WEATHER, result.shortlist)
        self.assertNotIn(MODEL_TASK_SCHEDULE, result.shortlist)
        self.assertNotIn(MODEL_TASK_LIST, result.shortlist)
        self.assertNotIn(MODEL_TASK_CANCEL, result.shortlist)
