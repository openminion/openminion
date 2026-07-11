from __future__ import annotations

import pytest
from typing import Any, get_args

from openminion.modules.memory.runtime.scope import (
    ScopeAccessDecision,
    ScopeAccessMode,
    ScopeBoundaryEvent,
    ScopeOperation,
    append_to_ledger,
    assert_scope_matches_agent,
    build_agent_read_scopes,
    build_agent_write_scope,
    drain_ledger,
    emit_read_decision,
    emit_write_decision,
    record_scope_boundary_event,
    resolve_namespace_filter,
    snapshot_ledger,
)
from openminion.modules.memory.errors import InvalidArgumentError, MemctlError
from openminion.modules.memory.models import MemoryNamespace


@pytest.fixture(autouse=True)
def _clear_ledger() -> None:
    drain_ledger()
    yield
    drain_ledger()


def _read_scopes(
    mode: Any,
    *,
    seam: str,
    agent_id: str = "agent-a",
    session_id: str | None = None,
    project_id: str | None = None,
) -> ScopeAccessDecision:
    return build_agent_read_scopes(
        agent_id,
        mode=mode,
        session_id=session_id,
        project_id=project_id,
        caller_seam=seam,
    )


class TestClosedSetLiteralDiscipline:
    def test_scope_access_mode_exhaustive_four_values(self) -> None:
        assert set(get_args(ScopeAccessMode)) == {
            "agent_only",
            "agent_plus_global",
            "session_plus_agent",
            "project_plus_agent",
        }

    def test_scope_operation_closed_set(self) -> None:
        assert set(get_args(ScopeOperation)) == {"read", "write"}

    def test_unknown_mode_raises(self) -> None:
        with pytest.raises(MemctlError, match="unknown ScopeAccessMode"):
            _read_scopes("agent_and_partial", seam="test.unknown_mode")

    def test_unknown_operation_raises(self) -> None:
        decision = _read_scopes("agent_only", seam="test.unknown_op")
        with pytest.raises(MemctlError, match="unknown ScopeOperation"):
            record_scope_boundary_event(
                decision,
                operation="delete",  # type: ignore[arg-type]
            )


class TestDeterministicScopeConstruction:
    def test_agent_only_yields_one_scope(self) -> None:
        decision = _read_scopes("agent_only", seam="test.agent_only")
        assert decision.scopes == ["agent:agent-a"]
        assert decision.mode == "agent_only"
        assert decision.agent_id == "agent-a"
        assert decision.caller_seam == "test.agent_only"

    def test_agent_plus_global_yields_both_scopes(self) -> None:
        decision = _read_scopes("agent_plus_global", seam="test.agent_plus_global")
        assert decision.scopes == ["agent:agent-a", "global:default"]

    def test_session_plus_agent_requires_session_id(self) -> None:
        with pytest.raises(MemctlError, match="session_plus_agent.*session_id"):
            _read_scopes("session_plus_agent", seam="test.session_plus_agent")

    def test_project_plus_agent_requires_project_id(self) -> None:
        with pytest.raises(MemctlError, match="project_plus_agent.*project_id"):
            _read_scopes("project_plus_agent", seam="test.project_plus_agent")

    def test_session_plus_agent_constructs_session_scope(self) -> None:
        decision = _read_scopes(
            "session_plus_agent",
            seam="test.session",
            session_id="sess-1",
        )
        assert decision.scopes == ["session:sess-1", "agent:agent-a"]

    def test_project_plus_agent_constructs_project_scope(self) -> None:
        decision = _read_scopes(
            "project_plus_agent",
            seam="test.project",
            project_id="proj-x",
        )
        assert decision.scopes == ["project:proj-x", "agent:agent-a"]

    def test_build_agent_write_scope_is_canonical(self) -> None:
        assert build_agent_write_scope("agent-a") == "agent:agent-a"
        assert build_agent_write_scope("  agent-b  ") == "agent:agent-b"

    def test_build_agent_write_scope_rejects_empty(self) -> None:
        with pytest.raises(MemctlError, match="agent_id"):
            build_agent_write_scope("")

    def test_caller_seam_must_be_non_empty(self) -> None:
        with pytest.raises(MemctlError, match="caller_seam"):
            _read_scopes("agent_only", seam="")

    def test_deterministic_inputs_yield_same_scopes(self) -> None:
        d1 = _read_scopes("agent_plus_global", seam="test.deterministic")
        d2 = _read_scopes("agent_plus_global", seam="test.deterministic")
        assert d1.scopes == d2.scopes
        assert d1.mode == d2.mode


class TestCrossAgentLeakPrevention:
    def test_assert_blocks_other_agent_scope(self) -> None:
        with pytest.raises(PermissionError, match="cross-agent scope leak"):
            assert_scope_matches_agent("agent:agent-a", "agent-b")

    def test_assert_passes_for_matching_agent(self) -> None:
        assert_scope_matches_agent("agent:agent-a", "agent-a")

    def test_assert_passes_for_non_agent_scope(self) -> None:
        assert_scope_matches_agent("session:sess-1", "agent-a")
        assert_scope_matches_agent("global:default", "agent-a")
        assert_scope_matches_agent("project:proj-x", "agent-a")

    def test_assert_rejects_empty_scope(self) -> None:
        with pytest.raises(MemctlError, match="scope must be a non-empty"):
            assert_scope_matches_agent("", "agent-a")


class TestNamespaceFilterResolution:
    def test_all_dimensions_round_trip_through_canonical_type(self) -> None:
        namespace = resolve_namespace_filter(
            namespace={
                "tenant_id": "tenant-a",
                "org_id": "org-a",
                "user_id": "user-a",
                "agent_id": "agent-a",
                "session_id": "session-a",
                "conversation_id": "conversation-a",
                "project_id": "project-a",
                "graph_id": "graph-a",
            }
        )

        assert isinstance(namespace, MemoryNamespace)
        assert namespace.as_dict() == {
            "tenant_id": "tenant-a",
            "org_id": "org-a",
            "user_id": "user-a",
            "agent_id": "agent-a",
            "session_id": "session-a",
            "conversation_id": "conversation-a",
            "project_id": "project-a",
            "graph_id": "graph-a",
        }

    @pytest.mark.parametrize(
        ("scope", "expected"),
        [
            ("session:s1", {"session_id": "s1"}),
            ("agent:a1", {"agent_id": "a1"}),
            ("project:p1", {"project_id": "p1"}),
            ("global:g1", {"graph_id": "g1"}),
        ],
    )
    def test_scope_only_uses_canonical_bridge(
        self, scope: str, expected: dict[str, str]
    ) -> None:
        assert resolve_namespace_filter(scope=scope).as_dict() == expected

    def test_matching_scope_merges_with_explicit_dimensions(self) -> None:
        namespace = resolve_namespace_filter(
            scope="agent:agent-a",
            namespace={"user_id": "user-a", "agent_id": "agent-a"},
        )

        assert namespace.as_dict() == {
            "user_id": "user-a",
            "agent_id": "agent-a",
        }

    def test_conflicting_scope_fails_closed(self) -> None:
        with pytest.raises(InvalidArgumentError, match="conflicting namespace agent_id"):
            resolve_namespace_filter(
                scope="agent:agent-a",
                namespace={"agent_id": "agent-b"},
            )

    @pytest.mark.parametrize("namespace", [{}, {"user_id": ""}])
    def test_empty_explicit_namespace_is_rejected(self, namespace) -> None:
        with pytest.raises(InvalidArgumentError, match="namespace must"):
            resolve_namespace_filter(namespace=namespace)

    def test_unknown_or_display_name_fields_are_rejected(self) -> None:
        with pytest.raises(InvalidArgumentError, match="display_name"):
            resolve_namespace_filter(namespace={"display_name": "Alice"})

    def test_non_string_ids_are_rejected(self) -> None:
        with pytest.raises(InvalidArgumentError, match="must be strings"):
            resolve_namespace_filter(namespace={"user_id": 123})

    def test_missing_scope_and_namespace_is_rejected(self) -> None:
        with pytest.raises(InvalidArgumentError, match="scope or namespace"):
            resolve_namespace_filter()


class TestSharedScopePolicySurfacing:
    def test_global_default_only_under_agent_plus_global(self) -> None:
        for mode in ("agent_only", "session_plus_agent", "project_plus_agent"):
            kwargs = {}
            if mode == "session_plus_agent":
                kwargs["session_id"] = "sess-1"
            if mode == "project_plus_agent":
                kwargs["project_id"] = "proj-x"
            decision = _read_scopes(mode, seam="test.no_silent_widen", **kwargs)
            assert "global:default" not in decision.scopes, (
                f"mode={mode} silently widened to global:default"
            )

    def test_agent_plus_global_surfaces_widening(self) -> None:
        decision = _read_scopes("agent_plus_global", seam="test.widen")
        assert "global:default" in decision.scopes
        assert decision.mode == "agent_plus_global"


class TestAuditEventEmission:
    def test_event_count_matches_widened_reads(self) -> None:
        widened_calls = [
            ("agent_plus_global", {}),
            ("session_plus_agent", {"session_id": "sess-1"}),
            ("project_plus_agent", {"project_id": "proj-x"}),
        ]
        for mode, kwargs in widened_calls:
            emit_read_decision(
                "agent-a",
                mode=mode,  # type: ignore[arg-type]
                caller_seam="test.widen_parity",
                **kwargs,
            )
        events = snapshot_ledger()
        assert len(events) == len(widened_calls)
        modes_emitted = {event.mode for event in events}
        assert modes_emitted == {
            "agent_plus_global",
            "session_plus_agent",
            "project_plus_agent",
        }

    def test_agent_only_read_does_not_emit_event(self) -> None:
        emit_read_decision(
            "agent-a",
            mode="agent_only",
            caller_seam="test.no_emit",
        )
        assert snapshot_ledger() == []

    def test_write_decision_always_emits_event(self) -> None:
        scope, event = emit_write_decision(
            "agent-a",
            caller_seam="test.write_emit",
        )
        assert scope == "agent:agent-a"
        assert event.operation == "write"
        assert event.agent_id == "agent-a"
        assert event.caller_seam == "test.write_emit"
        ledger = snapshot_ledger()
        assert len(ledger) == 1
        assert ledger[0].event_id == event.event_id

    def test_event_shape_conformance(self) -> None:
        decision = _read_scopes("agent_plus_global", seam="test.shape")
        event = record_scope_boundary_event(
            decision,
            operation="read",
        )
        assert isinstance(event, ScopeBoundaryEvent)
        assert event.event_id
        assert event.recorded_at
        assert event.scopes == decision.scopes
        assert event.mode == decision.mode
        assert event.caller_seam == decision.caller_seam

    def test_ledger_ordering_is_fifo(self) -> None:
        for i in range(3):
            emit_read_decision(
                f"agent-{i}",
                mode="agent_plus_global",
                caller_seam=f"test.fifo.{i}",
            )
        events = snapshot_ledger()
        assert [e.agent_id for e in events] == ["agent-0", "agent-1", "agent-2"]

    def test_drain_clears_ledger(self) -> None:
        emit_read_decision(
            "agent-a",
            mode="agent_plus_global",
            caller_seam="test.drain",
        )
        assert len(snapshot_ledger()) == 1
        drained = drain_ledger()
        assert len(drained) == 1
        assert snapshot_ledger() == []


class TestAntiLLMFieldNaming:
    def test_decision_has_no_prose_classification_fields(self) -> None:
        decision_fields = set(ScopeAccessDecision.model_fields.keys())
        expected_fields = {
            "agent_id",
            "mode",
            "scopes",
            "caller_seam",
            "decided_at",
        }
        assert decision_fields == expected_fields, (
            f"unexpected fields on ScopeAccessDecision: "
            f"{decision_fields - expected_fields}"
        )

    def test_event_has_no_prose_classification_fields(self) -> None:
        event_fields = set(ScopeBoundaryEvent.model_fields.keys())
        expected_fields = {
            "event_id",
            "agent_id",
            "mode",
            "scopes",
            "operation",
            "caller_seam",
            "recorded_at",
        }
        assert event_fields == expected_fields, (
            f"unexpected fields on ScopeBoundaryEvent: {event_fields - expected_fields}"
        )

    def test_caller_seam_is_caller_declared_not_synthesized(self) -> None:
        for seam in ("brain.read.alpha", "lifecycle.read.beta"):
            decision = _read_scopes("agent_plus_global", seam=seam)
            event = record_scope_boundary_event(
                decision,
                operation="read",
                audit_log=append_to_ledger,
            )
            assert event.caller_seam == seam


class TestServiceBoundaryGuardIntegration:
    def test_service_list_guard_blocks_cross_agent(self) -> None:
        from openminion.modules.memory.service import MemoryService
        from openminion.modules.memory.storage.base import ListQueryOptions

        class _NoopStore:
            def list(self, options):  # noqa: D401, ANN001
                raise AssertionError("store.list must not be reached")

        svc = MemoryService(store=_NoopStore())  # type: ignore[arg-type]
        with pytest.raises(PermissionError, match="cross-agent"):
            svc.list(
                ListQueryOptions(scopes=["agent:agent-other"]),
                agent_id="agent-self",
            )

    def test_service_write_record_guard_blocks_cross_agent(self) -> None:
        from openminion.modules.memory.service import MemoryService

        class _NoopStore:
            def put(self, record):  # noqa: D401, ANN001
                raise AssertionError("store.put must not be reached")

        svc = MemoryService(store=_NoopStore())  # type: ignore[arg-type]
        with pytest.raises(PermissionError, match="cross-agent"):
            svc.write_record(
                scope="agent:agent-other",
                record_type="fact",
                title="x",
                content="y",
                agent_id="agent-self",
            )
