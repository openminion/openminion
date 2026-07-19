from __future__ import annotations

import unittest
from dataclasses import dataclass
from typing import List, Optional
from unittest.mock import MagicMock

from openminion.base.config.base import ConfigError
from openminion.base.config.tool_selection import ToolSelectionConfig
from openminion.base.config.tool_selection.parser import _parse_tool_selection_config
from openminion.modules.llm.providers.base import ProviderToolSpec
from openminion.services.tool.selection import (
    SchemaExposure,
    SelectionMode,
    ToolSelectionService,
    create_validation_error,
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

    def _binding_manager(self):  # pragma: no cover — probed for schema only
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
    service._identity_filter_cache = {}
    # Override the live-registry probe so these tests run without a
    # wired tool registry manager.
    service._registry_specs = MagicMock(return_value=list(specs))  # type: ignore[method-assign]
    return service


class FullCatalogFallbackTests(unittest.TestCase):
    def test_no_typed_signal_typed_mode_exposes_full_catalog(self) -> None:
        service = _make_service(
            [_spec("alpha"), _spec("beta"), _spec("gamma")],
            mode=SelectionMode.TYPED,
        )
        result = service.select_tools(
            query="",
            intent_categories=None,
            forced_category=None,
        )
        self.assertEqual(result.mode, "typed")
        self.assertEqual(result.shortlist, ["alpha", "beta", "gamma"])
        self.assertIn("full_catalog", result.reason_codes)

    def test_no_typed_signal_deterministic_mode_returns_empty_shortlist(self) -> None:
        service = _make_service(
            [_spec("alpha"), _spec("beta")],
            mode=SelectionMode.DETERMINISTIC,
        )
        result = service.select_tools(query="", intent_categories=None)
        self.assertEqual(result.mode, "deterministic")
        self.assertEqual(result.shortlist, [])
        self.assertEqual(result.stubs, [])
        self.assertIn("no_typed_signal", result.reason_codes)
        # And crucially: full_catalog reason code is NOT present; this
        # was the post-TSSR regression the review caught.
        self.assertNotIn("full_catalog", result.reason_codes)

    def test_no_typed_signal_deterministic_mode_ignores_query_prose(self) -> None:
        # Strict deterministic must also not return anything even when
        # the query string contains tokens that would have matched tools
        # in the retired ranked scorer. Same empty result.
        service = _make_service(
            [_spec("alpha"), _spec("beta")],
            mode=SelectionMode.DETERMINISTIC,
        )
        result = service.select_tools(query="alpha please", intent_categories=None)
        self.assertEqual(result.shortlist, [])
        self.assertIn("no_typed_signal", result.reason_codes)

    def test_empty_registry_returns_no_tools_reason_code(self) -> None:
        service = _make_service([], mode=SelectionMode.TYPED)
        result = service.select_tools(query="", intent_categories=None)
        self.assertEqual(result.shortlist, [])
        self.assertIn("no_tools", result.reason_codes)

    def test_full_catalog_selection_ignores_query_string(self) -> None:
        # TSSR hard rule #1: the user query must not influence shortlist
        # composition on the typed-signal-absent path.
        service = _make_service(
            [_spec("alpha"), _spec("beta"), _spec("gamma")],
            mode=SelectionMode.TYPED,
        )
        result_empty = service.select_tools(query="", intent_categories=None)
        result_prose = service.select_tools(
            query="please find me a tool that does gamma things",
            intent_categories=None,
        )
        self.assertEqual(result_empty.shortlist, result_prose.shortlist)
        self.assertEqual(result_empty.shortlist, ["alpha", "beta", "gamma"])


class FullCatalogTruncationTests(unittest.TestCase):
    def test_budget_truncation_is_alphabetical_prefix(self) -> None:
        service = _make_service(
            [_spec(n) for n in ("zeta", "delta", "alpha", "echo", "bravo")],
            mode=SelectionMode.TYPED,
            max_tools_per_turn=3,
        )
        result = service.select_tools(query="", intent_categories=None)
        self.assertEqual(result.shortlist, ["alpha", "bravo", "delta"])
        truncation_markers = [
            c for c in result.reason_codes if c.startswith("truncated:")
        ]
        self.assertEqual(truncation_markers, ["truncated:2"])

    def test_budget_truncation_by_token_budget(self) -> None:
        # Moderate descriptions — each stub eats ~45 tokens; a budget of
        # ~50 fits exactly one alphabetically-first tool and truncates the
        # rest deterministically.
        service = _make_service(
            [
                _spec("alpha", "a" * 120),
                _spec("bravo", "b" * 120),
                _spec("charlie", "c" * 120),
            ],
            mode=SelectionMode.TYPED,
            tool_prompt_token_budget=100,
        )
        result = service.select_tools(query="", intent_categories=None)
        self.assertGreaterEqual(
            len(result.shortlist),
            1,
            "expected at least one tool to fit",
        )
        expected_prefix = ["alpha", "bravo", "charlie"][: len(result.shortlist)]
        self.assertEqual(result.shortlist, expected_prefix)
        if len(result.shortlist) < 3:
            truncation_markers = [
                c for c in result.reason_codes if c.startswith("truncated:")
            ]
            self.assertEqual(len(truncation_markers), 1)


class SchemaTieringTests(unittest.TestCase):
    def test_stub_first_exposes_stub_until_validation_error_requests_full_schema(
        self,
    ) -> None:
        service = _make_service(
            [_spec("alpha")],
            mode=SelectionMode.TYPED,
        )

        initial = service.select_tools(query="", intent_categories=None)
        self.assertEqual(initial.shortlist, ["alpha"])
        self.assertEqual(initial.full_schema_tools, [])
        self.assertEqual([stub.name for stub in initial.stubs], ["alpha"])

        error = create_validation_error(
            tool_name="alpha",
            missing_required=["path"],
            wrong_type=[],
        )
        self.assertTrue(service.should_expand_schema("alpha", error))
        expanded = service.get_full_schema("alpha")
        self.assertIsNotNone(expanded)
        self.assertEqual(expanded.name, "alpha")


class TypedSignalStillRoutesDeterministicTests(unittest.TestCase):
    def test_forced_category_typed_mode_hits_deterministic_path(self) -> None:
        service = _make_service(
            [_spec("weather.openmeteo")],
            mode=SelectionMode.TYPED,
            bindings={"weather": "weather.openmeteo"},
            category_entries={"weather.openmeteo": _FakeCategoryEntry("weather", [])},
        )
        result = service.select_tools(
            query="temperature in tokyo",
            forced_category="weather",
        )
        self.assertEqual(result.mode, "deterministic")
        self.assertEqual(result.shortlist, ["weather.openmeteo"])

    def test_intent_categories_typed_mode_hits_deterministic_path(self) -> None:
        service = _make_service(
            [_spec("weather.openmeteo")],
            mode=SelectionMode.TYPED,
            bindings={"weather": "weather.openmeteo"},
            category_entries={"weather.openmeteo": _FakeCategoryEntry("weather", [])},
        )
        result = service.select_tools(
            query="temperature in tokyo",
            intent_categories=["weather"],
        )
        self.assertEqual(result.mode, "deterministic")
        self.assertEqual(result.shortlist, ["weather.openmeteo"])


class LegacyModeMigrationTests(unittest.TestCase):
    def test_legacy_ranked_mode_raises_config_error_with_migration(self) -> None:
        with self.assertRaises(ConfigError) as cm:
            _parse_tool_selection_config({"mode": "ranked"})
        msg = str(cm.exception)
        self.assertIn("retired", msg)
        self.assertIn("typed", msg)
        self.assertIn("deterministic", msg)
        # Spec reference for operators following the migration trail
        self.assertIn("tool-selection migration guide", msg)

    def test_legacy_hybrid_mode_raises_config_error_with_migration(self) -> None:
        with self.assertRaises(ConfigError) as cm:
            _parse_tool_selection_config({"mode": "hybrid"})
        msg = str(cm.exception)
        self.assertIn("renamed", msg)
        self.assertIn("typed", msg)

    def test_typed_value_normalizes_clean(self) -> None:
        config = _parse_tool_selection_config({"mode": "typed"})
        self.assertEqual(config.mode, "typed")

    def test_deterministic_value_normalizes_clean(self) -> None:
        config = _parse_tool_selection_config({"mode": "deterministic"})
        self.assertEqual(config.mode, "deterministic")
