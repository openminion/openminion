from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from openminion.services.lifecycle.request_orchestrator import (
    _apply_workspace_root,
    _build_turn_context,
    _mutable_inbound_metadata,
)


def test_turn_context_is_immutable_with_boundary_conversion() -> None:
    context = _build_turn_context(
        message="hello",
        forced_tools=["web.search", "weather"],
        inbound_metadata={"session_id": "s1"},
    )

    assert context.message == "hello"
    assert context.forced_tools == ("web.search", "weather")
    assert dict(context.inbound_metadata or {}) == {"session_id": "s1"}

    with pytest.raises(FrozenInstanceError):
        context.message = "changed"  # type: ignore[misc]
    with pytest.raises(TypeError):
        assert context.inbound_metadata is not None
        context.inbound_metadata["session_id"] = "s2"  # type: ignore[index]

    mutable = _mutable_inbound_metadata(context.inbound_metadata)
    assert mutable == {"session_id": "s1"}
    assert mutable is not None
    mutable["session_id"] = "s2"
    assert dict(context.inbound_metadata or {}) == {"session_id": "s1"}


def test_apply_workspace_root_preserves_existing_value() -> None:
    existing = {"workspace_root": "/tmp/existing"}
    updated = _apply_workspace_root(
        inbound_metadata=existing,
        runtime_workspace_root="/tmp/new",
    )
    assert updated == {"workspace_root": "/tmp/existing"}
