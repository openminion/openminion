from __future__ import annotations

import inspect
import unittest

from openminion.modules.brain.loop.strategies.coding import handler
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


class CodingHandlerSurfaceTests(unittest.TestCase):
    def test_every_expected_symbol_resolves(self) -> None:
        for name in EXPECTED_HANDLER_SYMBOLS:
            with self.subTest(symbol=name):
                self.assertTrue(
                    hasattr(handler, name),
                    f"handler.py lost symbol `{name}` — split regression.",
                )

    def test_coding_mode_inherits_from_coding_profile_runner(self) -> None:
        self.assertTrue(issubclass(CodingMode, CodingProfileRunner))

    def test_coding_profile_runner_is_a_class(self) -> None:
        self.assertTrue(inspect.isclass(CodingProfileRunner))

    def test_entry_points_callable_with_single_ctx_arg(self) -> None:
        # Both `execute_coding_profile(ctx)` and `prepare_coding_profile(ctx)`
        # take ctx as the first positional argument. Lock the shape.
        for fn in (execute_coding_profile, prepare_coding_profile):
            with self.subTest(fn=fn.__name__):
                sig = inspect.signature(fn)
                params = list(sig.parameters.values())
                self.assertGreaterEqual(len(params), 1)
                self.assertEqual(params[0].name, "ctx")


# Section 2 — CodingProfileRunner method-surface pinning


EXPECTED_RUNNER_METHODS: tuple[str, ...] = (
    "prepare",
    "execute",
)


class CodingProfileRunnerMethodTests(unittest.TestCase):
    def test_runner_exposes_prepare_and_execute(self) -> None:
        for name in EXPECTED_RUNNER_METHODS:
            with self.subTest(method=name):
                self.assertTrue(
                    hasattr(CodingProfileRunner, name),
                    f"CodingProfileRunner lost method `{name}`.",
                )
                self.assertTrue(callable(getattr(CodingProfileRunner, name)))


# Section 3 — Pure-helper behavior pins (no ExecutionContext required)


class CodingHandlerPureHelperBehaviorTests(unittest.TestCase):
    def test_build_error_result_shape(self) -> None:
        result = handler._build_error_result("oops", "TEST_CODE")
        self.assertEqual(result.summary, "oops")
        self.assertIsNotNone(result.error)
        assert result.error is not None
        self.assertEqual(result.error.code, "TEST_CODE")

    def test_build_blocked_result_shape(self) -> None:
        result = handler._build_blocked_result("blocked", "TEST_CODE")
        self.assertEqual(result.summary, "blocked")
        # Blocked vs error is signaled by status, not by presence of error.
        from openminion.modules.brain.constants import BRAIN_ACTION_STATUS_BLOCKED

        self.assertEqual(result.status, BRAIN_ACTION_STATUS_BLOCKED)

    def test_build_tool_specs_returns_a_list(self) -> None:
        # frozenset() input → empty list; ensures the function is callable
        # and returns a sequence shape.
        specs = handler._build_tool_specs(frozenset())
        self.assertIsInstance(specs, list)

    def test_build_tool_specs_encodes_file_vs_shell_scaffolding_boundary(self) -> None:
        specs = handler._build_tool_specs(frozenset({"file.write", "exec.run"}))
        by_name = {spec.name: spec for spec in specs}

        self.assertIn("parent directories", by_name["file.write"].description)
        self.assertIn("scaffold", by_name["file.write"].description.lower())
        self.assertIn("structured file tools", by_name["exec.run"].description)
        self.assertIn("directories", by_name["exec.run"].description)
