from __future__ import annotations

import openminion.modules.tool as tool_facade


P8_HOISTED_SYMBOLS = (
    "ToolRegistry",
    "ToolExecutionContext",
    "resolve_binding_for_call",
    "build_default_tool_registry",
    "Policy",
    "DEFAULT_POLICY",
    "canonical_tool_name",
    "ToolSpec",
    "RuntimeContext",
    "build_runtime_repositories",
    "create_run_root",
    "new_run_id",
    "preferred_artifact_ref",
)


def test_p8_hoisted_symbols_exposed_on_facade() -> None:
    missing = [name for name in P8_HOISTED_SYMBOLS if not hasattr(tool_facade, name)]
    assert not missing, f"Missing facade exports after P8 hoist: {missing}"


def test_p8_hoisted_symbols_listed_in_dunder_all() -> None:
    exported = set(getattr(tool_facade, "__all__", ()))
    missing = [name for name in P8_HOISTED_SYMBOLS if name not in exported]
    assert not missing, f"Hoisted symbols missing from tool.__all__: {missing}"


def test_p8_facade_objects_are_identity_equal_to_deep_definitions() -> None:
    from openminion.modules.tool.base import (
        ToolExecutionContext as _ToolExecutionContext,
    )
    from openminion.modules.tool.registry import (
        ToolRegistry as _ToolRegistry,
        ToolSpec as _ToolSpec,
    )
    from openminion.modules.tool.runtime import (
        RuntimeContext as _RuntimeContext,
        build_runtime_repositories as _build_runtime_repositories,
        create_run_root as _create_run_root,
        new_run_id as _new_run_id,
        preferred_artifact_ref as _preferred_artifact_ref,
    )
    from openminion.modules.tool.runtime.dispatch import (
        resolve_binding_for_call as _resolve_binding_for_call,
    )
    from openminion.modules.tool.runtime.policy import (
        DEFAULT_POLICY as _DEFAULT_POLICY,
        Policy as _Policy,
        canonical_tool_name as _canonical_tool_name,
    )

    assert tool_facade.ToolExecutionContext is _ToolExecutionContext
    assert tool_facade.ToolRegistry is _ToolRegistry
    assert tool_facade.ToolSpec is _ToolSpec
    assert tool_facade.RuntimeContext is _RuntimeContext
    assert tool_facade.build_runtime_repositories is _build_runtime_repositories
    assert tool_facade.create_run_root is _create_run_root
    assert tool_facade.new_run_id is _new_run_id
    assert tool_facade.preferred_artifact_ref is _preferred_artifact_ref
    assert tool_facade.resolve_binding_for_call is _resolve_binding_for_call
    assert tool_facade.DEFAULT_POLICY is _DEFAULT_POLICY
    assert tool_facade.Policy is _Policy
    assert tool_facade.canonical_tool_name is _canonical_tool_name


def test_brain_adapter_imports_through_facade() -> None:
    import inspect

    from openminion.modules.brain.adapters.tool import runtime as brain_tool_runtime

    source = inspect.getsource(brain_tool_runtime)

    forbidden_deep_imports = (
        "from openminion.modules.tool.runtime.dispatch import",
        "from openminion.modules.tool.runtime.policy import",
        "from openminion.modules.tool.registry import",
        "from openminion.modules.tool.runtime import",
        "from openminion.modules.tool.base import",
    )
    regressions = [pattern for pattern in forbidden_deep_imports if pattern in source]
    assert not regressions, (
        f"brain/adapters/tool/runtime.py regressed to deep imports: {regressions}. "
        "Re-import these symbols from `openminion.modules.tool` instead."
    )
