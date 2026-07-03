from __future__ import annotations

import inspect
from unittest.mock import patch

import pytest

from openminion.modules.brain.loop.strategies.coding import handler
from openminion.modules.brain.loop.strategies.coding import runtime as coding_runtime
from openminion.modules.brain.loop.strategies.coding.handler import (
    CodingMode,
    CodingProfileRunner,
    execute_coding_profile,
    prepare_coding_profile,
)


# Public symbols downstream imports rely on. Anything in this list MUST
# remain importable from `...coding.handler` after the split.
EXPECTED_HANDLER_SYMBOLS: tuple[str, ...] = (
    # Public entry points (also re-exported from the package __init__).
    "execute_coding_profile",
    "prepare_coding_profile",
    # Public classes.
    "CodingProfileRunner",
    "CodingMode",
    # Module-private helpers consumed by other modules in the package.
    # These are file-internal today; if the split moves them to a sibling
    # file they must still be importable from `handler` (shim re-export).
    "_CodingLoopContextAdapter",
    "_runner_and_profile_from_context",
    "_coding_mode_config_from_context",
    "_configured_coding_profile_runner",
    "_build_error_result",
    "_build_blocked_result",
    "_resolve_model",
    "_build_tool_specs",
    "_is_budget_exhausted",
)


class TestCodingHandlerSurface:
    @pytest.mark.parametrize("name", EXPECTED_HANDLER_SYMBOLS)
    def test_every_expected_symbol_resolves(self, name: str) -> None:
        assert hasattr(handler, name), f"handler.py lost symbol `{name}`."

    def test_coding_mode_inherits_from_coding_profile_runner(self) -> None:
        assert issubclass(CodingMode, CodingProfileRunner)

    def test_coding_profile_runner_is_a_class(self) -> None:
        assert inspect.isclass(CodingProfileRunner)

    @pytest.mark.parametrize("fn", [execute_coding_profile, prepare_coding_profile])
    def test_entry_points_callable_with_single_ctx_arg(self, fn) -> None:
        # Both `execute_coding_profile(ctx)` and `prepare_coding_profile(ctx)`
        # take ctx as the first positional argument. Lock the shape.
        sig = inspect.signature(fn)
        params = list(sig.parameters.values())
        assert len(params) >= 1
        assert params[0].name == "ctx"


EXPECTED_RUNNER_METHODS: tuple[str, ...] = (
    "prepare",
    "execute",
)


class TestCodingProfileRunnerMethods:
    @pytest.mark.parametrize("name", EXPECTED_RUNNER_METHODS)
    def test_runner_exposes_prepare_and_execute(self, name: str) -> None:
        assert hasattr(CodingProfileRunner, name), (
            f"CodingProfileRunner lost method `{name}`."
        )
        assert callable(getattr(CodingProfileRunner, name))


class TestCodingHandlerPureHelperBehavior:
    def test_build_error_result_shape(self) -> None:
        result = handler._build_error_result("oops", "TEST_CODE")
        assert result.summary == "oops"
        assert result.error is not None
        assert result.error.code == "TEST_CODE"

    def test_build_blocked_result_shape(self) -> None:
        result = handler._build_blocked_result("blocked", "TEST_CODE")
        assert result.summary == "blocked"
        # Blocked vs error is signaled by status, not by presence of error.
        from openminion.modules.brain.constants import BRAIN_ACTION_STATUS_BLOCKED

        assert result.status == BRAIN_ACTION_STATUS_BLOCKED

    def test_build_tool_specs_returns_a_list(self) -> None:
        specs = handler._build_tool_specs(frozenset())
        assert isinstance(specs, list)

    def test_build_tool_specs_encodes_file_vs_shell_scaffolding_boundary(self) -> None:
        specs = handler._build_tool_specs(frozenset({"file.write", "exec.run"}))
        by_name = {spec.name: spec for spec in specs}

        assert "parent directories" in by_name["file.write"].description
        assert "scaffold" in by_name["file.write"].description.lower()
        assert "structured file tools" in by_name["exec.run"].description
        assert "directories" in by_name["exec.run"].description

    def test_build_tool_specs_uses_runtime_schema_when_available(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "workdir": {"type": "string"},
            },
            "required": ["command"],
            "additionalProperties": False,
        }
        with (
            patch.object(
                coding_runtime,
                "_runner_and_profile_from_context",
                return_value=(object(), None),
            ),
            patch.object(
                coding_runtime,
                "collect_runtime_tool_schemas",
                return_value=[
                    {
                        "name": "exec.run",
                        "parameters": schema,
                    }
                ],
            ),
        ):
            specs = handler._build_tool_specs(frozenset({"exec.run"}), ctx=object())

        [spec] = specs
        assert spec.input_schema == schema
        assert "path/cwd/working_directory" in spec.description
