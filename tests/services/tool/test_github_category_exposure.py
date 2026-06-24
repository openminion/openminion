from __future__ import annotations

import unittest
from dataclasses import dataclass
from typing import List, Optional
from unittest.mock import MagicMock

from openminion.base.config.tool_selection import ToolSelectionConfig
from openminion.modules.llm.providers.base import ProviderToolSpec
from openminion.modules.tool.contracts.model_ids import (
    MODEL_GITHUB_COMMIT_FILES,
    MODEL_GITHUB_FETCH_CHECKS,
    MODEL_GITHUB_FETCH_COMMENTS,
    MODEL_GITHUB_FETCH_DIFF,
    MODEL_GITHUB_FETCH_PR,
    MODEL_GITHUB_LIST_PRS,
    MODEL_GITHUB_OPEN_PR,
    MODEL_GITHUB_POST_PR_COMMENT,
    MODEL_GITHUB_POST_PR_REVIEW,
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
    specs: list[ProviderToolSpec],
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


class GithubCategoryPreferenceTableTests(unittest.TestCase):
    def test_list_prs_category_lists_model_tool(self) -> None:
        self.assertIn("github.list_prs", _PREFERRED_MODEL_TOOLS_BY_CATEGORY)
        self.assertEqual(
            _PREFERRED_MODEL_TOOLS_BY_CATEGORY["github.list_prs"],
            [MODEL_GITHUB_LIST_PRS],
        )

    def test_fetch_pr_category_lists_model_tool(self) -> None:
        self.assertIn("github.fetch_pr", _PREFERRED_MODEL_TOOLS_BY_CATEGORY)
        self.assertEqual(
            _PREFERRED_MODEL_TOOLS_BY_CATEGORY["github.fetch_pr"],
            [MODEL_GITHUB_FETCH_PR],
        )

    def test_fetch_diff_category_lists_model_tool(self) -> None:
        self.assertIn("github.fetch_diff", _PREFERRED_MODEL_TOOLS_BY_CATEGORY)
        self.assertEqual(
            _PREFERRED_MODEL_TOOLS_BY_CATEGORY["github.fetch_diff"],
            [MODEL_GITHUB_FETCH_DIFF],
        )

    def test_fetch_comments_category_lists_model_tool(self) -> None:
        self.assertIn("github.fetch_comments", _PREFERRED_MODEL_TOOLS_BY_CATEGORY)
        self.assertEqual(
            _PREFERRED_MODEL_TOOLS_BY_CATEGORY["github.fetch_comments"],
            [MODEL_GITHUB_FETCH_COMMENTS],
        )

    def test_fetch_checks_category_lists_model_tool(self) -> None:
        self.assertIn("github.fetch_checks", _PREFERRED_MODEL_TOOLS_BY_CATEGORY)
        self.assertEqual(
            _PREFERRED_MODEL_TOOLS_BY_CATEGORY["github.fetch_checks"],
            [MODEL_GITHUB_FETCH_CHECKS],
        )

    def test_commit_files_category_lists_model_tool(self) -> None:
        self.assertIn("github.commit_files", _PREFERRED_MODEL_TOOLS_BY_CATEGORY)
        self.assertEqual(
            _PREFERRED_MODEL_TOOLS_BY_CATEGORY["github.commit_files"],
            [MODEL_GITHUB_COMMIT_FILES],
        )

    def test_open_pr_category_lists_model_tool(self) -> None:
        self.assertIn("github.open_pr", _PREFERRED_MODEL_TOOLS_BY_CATEGORY)
        self.assertEqual(
            _PREFERRED_MODEL_TOOLS_BY_CATEGORY["github.open_pr"],
            [MODEL_GITHUB_OPEN_PR],
        )

    def test_post_pr_review_category_lists_model_tool(self) -> None:
        self.assertIn("github.post_pr_review", _PREFERRED_MODEL_TOOLS_BY_CATEGORY)
        self.assertEqual(
            _PREFERRED_MODEL_TOOLS_BY_CATEGORY["github.post_pr_review"],
            [MODEL_GITHUB_POST_PR_REVIEW],
        )

    def test_post_pr_comment_category_lists_model_tool(self) -> None:
        self.assertIn("github.post_pr_comment", _PREFERRED_MODEL_TOOLS_BY_CATEGORY)
        self.assertEqual(
            _PREFERRED_MODEL_TOOLS_BY_CATEGORY["github.post_pr_comment"],
            [MODEL_GITHUB_POST_PR_COMMENT],
        )


class GithubDeterministicSelectionTests(unittest.TestCase):
    def test_list_prs_intent_category_selects_list_prs_tool(self) -> None:
        service = _make_service(
            [_spec(MODEL_GITHUB_LIST_PRS), _spec(MODEL_WEATHER)],
            mode=SelectionMode.TYPED,
            bindings={"github.list_prs": MODEL_GITHUB_LIST_PRS},
            category_entries={
                MODEL_GITHUB_LIST_PRS: _FakeCategoryEntry("github.list_prs", []),
                MODEL_WEATHER: _FakeCategoryEntry("weather", []),
            },
        )
        result = service.select_tools(
            query="show open PRs", intent_categories=["github.list_prs"]
        )
        self.assertEqual(result.mode, "deterministic")
        self.assertIn(MODEL_GITHUB_LIST_PRS, result.shortlist)
        self.assertNotIn(MODEL_WEATHER, result.shortlist)

    def test_unrelated_category_does_not_pull_github_tools(self) -> None:
        service = _make_service(
            [
                _spec(MODEL_WEATHER),
                _spec(MODEL_GITHUB_LIST_PRS),
                _spec(MODEL_GITHUB_FETCH_PR),
            ],
            mode=SelectionMode.TYPED,
            bindings={"weather": MODEL_WEATHER},
            category_entries={
                MODEL_WEATHER: _FakeCategoryEntry("weather", []),
                MODEL_GITHUB_LIST_PRS: _FakeCategoryEntry("github.list_prs", []),
                MODEL_GITHUB_FETCH_PR: _FakeCategoryEntry("github.fetch_pr", []),
            },
        )
        result = service.select_tools(
            query="temperature in Tokyo", forced_category="weather"
        )
        self.assertEqual(result.mode, "deterministic")
        self.assertIn(MODEL_WEATHER, result.shortlist)
        self.assertNotIn(MODEL_GITHUB_LIST_PRS, result.shortlist)
        self.assertNotIn(MODEL_GITHUB_FETCH_PR, result.shortlist)

    def test_read_only_tool_use_filters_github_write_tools(self) -> None:
        service = _make_service(
            [
                _spec(MODEL_GITHUB_COMMIT_FILES),
                _spec(MODEL_GITHUB_OPEN_PR),
                _spec(MODEL_GITHUB_POST_PR_REVIEW),
                _spec(MODEL_GITHUB_POST_PR_COMMENT),
                _spec(MODEL_GITHUB_LIST_PRS),
            ],
            mode=SelectionMode.TYPED,
            category_entries={
                MODEL_GITHUB_COMMIT_FILES: _FakeCategoryEntry(
                    "github.commit_files", []
                ),
                MODEL_GITHUB_OPEN_PR: _FakeCategoryEntry("github.open_pr", []),
                MODEL_GITHUB_POST_PR_REVIEW: _FakeCategoryEntry(
                    "github.post_pr_review", []
                ),
                MODEL_GITHUB_POST_PR_COMMENT: _FakeCategoryEntry(
                    "github.post_pr_comment", []
                ),
                MODEL_GITHUB_LIST_PRS: _FakeCategoryEntry("github.list_prs", []),
            },
        )
        result = service.select_tools(
            query="show repo tools",
            specs=[
                _spec(MODEL_GITHUB_COMMIT_FILES),
                _spec(MODEL_GITHUB_OPEN_PR),
                _spec(MODEL_GITHUB_POST_PR_REVIEW),
                _spec(MODEL_GITHUB_POST_PR_COMMENT),
                _spec(MODEL_GITHUB_LIST_PRS),
            ],
            tool_use_type="read_only",
        )
        self.assertIn(MODEL_GITHUB_LIST_PRS, result.shortlist)
        self.assertNotIn(MODEL_GITHUB_COMMIT_FILES, result.shortlist)
        self.assertNotIn(MODEL_GITHUB_OPEN_PR, result.shortlist)
        self.assertNotIn(MODEL_GITHUB_POST_PR_REVIEW, result.shortlist)
        self.assertNotIn(MODEL_GITHUB_POST_PR_COMMENT, result.shortlist)
