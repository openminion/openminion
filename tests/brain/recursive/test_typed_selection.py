from __future__ import annotations

from openminion.modules.brain.loop.recursive import (
    BRAIN_LOOP_RECURSIVE_SELECTION_MODE,
)
from openminion.modules.brain.schemas.state import BrainMode


def test_selection_signal_is_the_brain_mode_enum_value() -> None:

    assert BRAIN_LOOP_RECURSIVE_SELECTION_MODE is BrainMode.AUTONOMOUS
    assert isinstance(BRAIN_LOOP_RECURSIVE_SELECTION_MODE, BrainMode)
    # The value is a documented public enum value, not a scratch string.
    assert BRAIN_LOOP_RECURSIVE_SELECTION_MODE.value == "autonomous"


def test_selection_signal_is_exported_from_canonical_owner() -> None:

    import openminion.modules.brain.loop.recursive as canonical

    assert canonical.BRAIN_LOOP_RECURSIVE_SELECTION_MODE is BrainMode.AUTONOMOUS
    assert "BRAIN_LOOP_RECURSIVE_SELECTION_MODE" in canonical.__all__


def test_orchestrator_selects_recursive_via_typed_mode_only() -> None:

    import inspect

    from openminion.modules.brain.runner.tick import orchestrator

    source = inspect.getsource(orchestrator)
    # The exact comparison pattern in orchestrator.py:157-189.
    assert "autonomous" in source
    assert "rlm_api is not None" in source
    # Negative pin: no query-content or intent-inference gating.
    assert "query.lower()" not in source
    assert "user_input.lower()" not in source
    assert "intent_classifier" not in source


def test_recursive_family_owner_re_exports_rlm_service_surface() -> None:

    from openminion.modules.brain.loop import recursive

    # Spec §5.2 target shape: service/contracts/schemas/config need to
    # be resolvable through the canonical owner module before BRLI-03
    # physically moves the files.
    for name in (
        "RLMService",
        "RLMConfig",
        "RLMTelemetry",
        "RLMResponse",
        "RLMBudgets",
        "MetaDirective",
        "RetrievalFilters",
        "MemoryWriteIntent",
        "EvidenceRef",
        "TaskState",
        "WMState",
        "RLM_INTERFACE_VERSION",
    ):
        assert hasattr(recursive, name), (
            f"brain/loop/recursive/__init__.py missing BRLI-02 re-export {name!r}"
        )
