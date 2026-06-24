from __future__ import annotations

import os
from typing import Any
from unittest import mock

import pytest


class LaneAssertionError(AssertionError):
    def __init__(
        self,
        message: str,
        expected_lane: str,
        actual_lane: str,
        context: dict | None = None,
    ):
        super().__init__(message)
        self.expected_lane = expected_lane
        self.actual_lane = actual_lane
        self.context = context or {}


def assert_module_lane(
    runtime_mode: str,
    fallback_reason: str | None,
    source: str = "unknown",
    strict: bool = True,
) -> None:
    is_legacy = runtime_mode == "legacy"
    has_fallback_reason = bool(fallback_reason and fallback_reason.strip())

    if is_legacy:
        # Check if this is explicit opt-in vs unexpected fallback
        explicit_opt_in = has_fallback_reason and fallback_reason == "explicit_opt_in"

        if strict and not explicit_opt_in:
            raise LaneAssertionError(
                message=f"Unexpected legacy runtime mode detected in {source}. "
                f"Mode='{runtime_mode}', fallback_reason='{fallback_reason}'. "
                "Default mode should use module (brain) lane.",
                expected_lane="module",
                actual_lane="legacy",
                context={
                    "source": source,
                    "runtime_mode": runtime_mode,
                    "fallback_reason": fallback_reason,
                    "explicit_opt_in": explicit_opt_in,
                },
            )

    # Verify we have a valid module mode
    valid_module_modes = {"brain", "brain-bridge", "bridge"}
    if runtime_mode not in valid_module_modes and runtime_mode != "legacy":
        raise LaneAssertionError(
            message=f"Unknown runtime mode '{runtime_mode}' in {source}. "
            f"Expected one of: {valid_module_modes | {'legacy'}}",
            expected_lane="module",
            actual_lane=runtime_mode,
            context={
                "source": source,
                "runtime_mode": runtime_mode,
                "valid_modes": valid_module_modes,
            },
        )


def get_runtime_mode_from_env() -> tuple[str, bool]:
    runtime_mode = (
        os.environ.get("OPENMINION_AGENT_RUNTIME_MODE", "brain").strip().lower()
    )
    return runtime_mode, False


def is_explicit_legacy_opt_in() -> bool:
    runtime_mode = os.environ.get("OPENMINION_AGENT_RUNTIME_MODE", "").strip().lower()
    return runtime_mode == "legacy"


class NoLegacyTestContext:
    def __init__(self, source: str = "test", strict: bool = True):
        self.source = source
        self.strict = strict
        self.assertions_made: list[dict] = []

    def __enter__(self) -> "NoLegacyTestContext":
        runtime_mode, _ = get_runtime_mode_from_env()

        if runtime_mode == "legacy" and self.strict:
            raise LaneAssertionError(
                message=f"Cannot run no-legacy test in legacy mode (source={self.source}). "
                "Set OPENMINION_AGENT_RUNTIME_MODE=brain for module-first testing.",
                expected_lane="module",
                actual_lane="legacy",
                context={"source": self.source, "env_mode": runtime_mode},
            )
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        pass

    def assert_module_lane(
        self,
        runtime_mode: str,
        fallback_reason: str | None = None,
        additional_context: dict | None = None,
    ) -> None:
        assert_module_lane(
            runtime_mode=runtime_mode,
            fallback_reason=fallback_reason,
            source=self.source,
            strict=self.strict,
        )
        self.assertions_made.append(
            {
                "runtime_mode": runtime_mode,
                "fallback_reason": fallback_reason,
                "context": additional_context,
            }
        )

    def assert_no_implicit_fallback(self, fallback_reason: str | None) -> None:
        if not fallback_reason:
            return  # No fallback occurred

        # List of reasons that indicate explicit opt-in vs implicit fallback
        explicit_reasons = {"explicit_opt_in"}
        implicit_reasons = {
            "module_import_error",
            "brain_bridge_unavailable",
            "missing_dependency",
            "config_error",
        }

        # Check if this looks like an implicit fallback
        is_implicit = fallback_reason not in explicit_reasons and any(
            ir in fallback_reason.lower() for ir in implicit_reasons
        )

        if is_implicit and self.strict:
            raise LaneAssertionError(
                message=f"Implicit legacy fallback detected in {self.source}. "
                f"Fallback reason: '{fallback_reason}'. "
                "Strict mode requires fail-fast, not silent downgrade.",
                expected_lane="module",
                actual_lane="legacy",
                context={
                    "source": self.source,
                    "fallback_reason": fallback_reason,
                    "is_implicit": True,
                },
            )


@pytest.fixture
def no_legacy_test_context():
    with NoLegacyTestContext(source="pytest", strict=True) as ctx:
        yield ctx


@pytest.fixture
def enforce_module_first_env():
    env_vars = {
        "OPENMINION_AGENT_RUNTIME_MODE": "brain",
    }
    with mock.patch.dict(os.environ, env_vars, clear=False):
        yield


@pytest.fixture
def enforce_strict_module_env():
    env_vars = {
        "OPENMINION_AGENT_RUNTIME_MODE": "brain",
    }
    with mock.patch.dict(os.environ, env_vars, clear=False):
        yield


@pytest.fixture
def explicit_legacy_opt_in_env():
    env_vars = {
        "OPENMINION_AGENT_RUNTIME_MODE": "legacy",
    }
    with mock.patch.dict(os.environ, env_vars, clear=False):
        yield


def extract_runtime_info_from_api_runtime(runtime: Any) -> dict:
    return {
        "runtime_mode": getattr(runtime, "_runtime_mode", "unknown"),
        "fallback_reason": getattr(runtime, "_last_bridge_fallback_reason", ""),
        "brain_bridge_active": getattr(runtime, "_brain_bridge_active", False),
    }


def extract_runtime_info_from_agent_service(agent_service: Any) -> dict:
    service_type = type(agent_service).__name__
    is_bridge = service_type == "BrainBridgeService" or hasattr(
        agent_service, "_bridge_initialized"
    )

    return {
        "runtime_mode": "brain" if is_bridge else "legacy",
        "is_bridge_service": is_bridge,
        "service_type": service_type,
    }
