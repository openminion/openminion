from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import importlib
from pathlib import Path
from subprocess import run
import sys

import pytest

from openminion.api.handoff import SubagentRunContext, subagent
from openminion.modules.memory.adapters import (
    DelegatedContextBudgetError,
    DelegatedMemoryProposal,
    OpenMinionDelegationMemoryGrantResolver,
    authorize_and_enforce_delegated_context,
    emit_delegated_memory_session_event,
    enforce_delegated_context_budget,
    map_delegated_memory_transport,
    submit_delegated_memory_proposal,
)
from openminion.modules.memory.errors import (
    ConstraintViolationError,
    InvalidArgumentError,
)
from openminion.modules.memory.observability import DelegatedMemoryTelemetryBridge
from openminion.modules.policy.models import PolicyConfig, PolicyGrantInput
from openminion.modules.policy.runtime.service import PolicyCtl
from openminion.modules.session.storage.sqlite_store import SQLiteSessionStore
from sophiagraph.access import (
    AccessConstraint,
    AuthorizedSophiaGraphGateway,
    DelegatedMemoryAccessDeniedError,
    MemoryAccessContext,
    MemoryAccessRequest,
)
from sophiagraph.models import MemoryNamespace
from sophiagraph.storage import SophiaGraphMemoryStore, SophiaGraphSqliteStore


def test_memory_models_do_not_require_private_scope_pattern(monkeypatch) -> None:
    import openminion.modules.memory.models as openminion_models
    import sophiagraph.models as sophiagraph_models

    monkeypatch.delattr(sophiagraph_models, "_SCOPE_PATTERN")
    reloaded = importlib.reload(openminion_models)

    assert reloaded.SCOPE_PATTERN == sophiagraph_models.SCOPE_PATTERN


def _namespace() -> MemoryNamespace:
    return MemoryNamespace(agent_id="child", project_id="project", graph_id="main")


def _run_context(**overrides) -> SubagentRunContext:
    values = {
        "context_id": "context-child",
        "parent_agent_id": "parent",
        "child_agent_id": "child",
        "parent_run_id": "parent-run",
        "child_run_id": "child-run",
        "trace_parent_id": "trace",
        "memory_posture": "read_only_bounded",
        "memory_grant_id": "grant-placeholder",
    }
    values.update(overrides)
    return SubagentRunContext(**values)


def _target(namespace: MemoryNamespace) -> dict:
    return {
        "resource": "sophiagraph",
        "delegated_memory": {
            "version": 1,
            "audience": "sophiagraph",
            "delegator_agent_id": "parent",
            "subject_agent_id": "child",
            "parent_run_id": "parent-run",
            "child_run_id": "child-run",
            "trace_parent_id": "trace",
            "namespaces": [namespace.as_dict()],
            "workspace_ids": ["workspace"],
            "operations": ["read"],
            "record_types": ["fact"],
            "max_results": 3,
            "max_context_tokens": 8,
            "max_depth": 1,
            "can_reshare": False,
        },
    }


def _policy_with_grant(
    tmp_path: Path,
    *,
    namespace: MemoryNamespace | None = None,
) -> tuple[PolicyCtl, str]:
    policy = PolicyCtl.with_sqlite(
        tmp_path / "policy.db", config=PolicyConfig(mode="enforce")
    )
    grant_id = policy.create_grant(
        PolicyGrantInput(
            effect="allow",
            subject_id="child",
            tool="memory",
            method="delegated_read",
            target_json=_target(namespace or _namespace()),
            duration_type="until",
            expires_at=(datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        )
    )
    return policy, grant_id


def _access_context() -> MemoryAccessContext:
    return MemoryAccessContext(
        principal_id="child-principal",
        audience="sophiagraph",
        subject_agent_id="child",
        parent_run_id="parent-run",
        child_run_id="child-run",
        trace_parent_id="trace",
        constraints=(
            AccessConstraint(
                mode="allowlist",
                namespaces=(_namespace(),),
                workspace_ids=("workspace",),
                operations=("read",),
                record_types=("fact",),
                max_results=2,
                max_context_tokens=6,
            ),
        ),
        delegated=True,
        host_max_results=5,
        host_max_context_tokens=7,
    )


def _request(grant_id: str) -> MemoryAccessRequest:
    return MemoryAccessRequest(
        operation="read",
        grant_id=grant_id,
        namespaces=(_namespace(),),
        workspace_ids=("workspace",),
        record_types=("fact",),
        max_results=4,
        max_context_tokens=10,
    )


def test_policy_resolver_validates_lineage_before_consuming(tmp_path: Path) -> None:
    policy, grant_id = _policy_with_grant(tmp_path)
    try:
        wrong = OpenMinionDelegationMemoryGrantResolver(
            policy,
            _run_context(memory_grant_id=grant_id, child_run_id="copied-run"),
            memory_scope_namespaces=(_namespace(),),
        )
        assert (
            wrong.resolve_grant(grant_id, context=_access_context(), operation="read")
            is None
        )
        assert policy.list_grants()[0].uses_count == 0

        resolver = OpenMinionDelegationMemoryGrantResolver(
            policy,
            _run_context(memory_grant_id=grant_id),
            memory_scope_namespaces=(_namespace(),),
        )
        projected = resolver.resolve_grant(
            grant_id, context=_access_context(), operation="read"
        )
        assert projected is not None
        assert projected.max_results == 3
        assert policy.list_grants()[0].uses_count == 1
    finally:
        policy.close()


def test_none_and_cancelled_postures_never_resolve(tmp_path: Path) -> None:
    policy, grant_id = _policy_with_grant(tmp_path)
    try:
        for context in (
            _run_context(memory_posture="none", memory_grant_id=None),
            _run_context(memory_grant_id=grant_id, cancelled=True),
        ):
            resolver = OpenMinionDelegationMemoryGrantResolver(
                policy,
                context,
                memory_scope_namespaces=(_namespace(),),
            )
            assert (
                resolver.resolve_grant(
                    grant_id, context=_access_context(), operation="read"
                )
                is None
            )
        assert policy.list_grants()[0].uses_count == 0
    finally:
        policy.close()


def test_policy_resolver_intersects_grant_with_narrower_memory_scope(
    tmp_path: Path,
) -> None:
    policy, grant_id = _policy_with_grant(
        tmp_path,
        namespace=MemoryNamespace(project_id="project"),
    )
    try:
        resolver = OpenMinionDelegationMemoryGrantResolver(
            policy,
            _run_context(memory_grant_id=grant_id),
            memory_scope_namespaces=(_namespace(),),
        )
        projected = resolver.resolve_grant(
            grant_id,
            context=_access_context(),
            operation="read",
        )

        assert projected is not None
        assert projected.namespaces == (_namespace(),)
    finally:
        policy.close()


@dataclass(frozen=True)
class _Segment:
    id: str
    token_estimate: int


def test_context_assembly_intersects_budget_and_refreshes_revocation(
    tmp_path: Path,
) -> None:
    policy, grant_id = _policy_with_grant(tmp_path)
    try:
        resolver = OpenMinionDelegationMemoryGrantResolver(
            policy,
            _run_context(memory_grant_id=grant_id),
            memory_scope_namespaces=(_namespace(),),
        )
        gateway = AuthorizedSophiaGraphGateway(
            SophiaGraphMemoryStore(), resolver=resolver
        )
        result = authorize_and_enforce_delegated_context(
            gateway,
            [_Segment("one", 4), _Segment("two", 3), _Segment("three", 2)],
            context=_access_context(),
            request=_request(grant_id),
        )
        assert [item.id for item in result.segments] == ["one", "three"]
        assert result.max_context_tokens == 6
        assert result.omitted_segment_ids == ("two",)

        assert policy.revoke_grant(grant_id)
        with pytest.raises(DelegatedMemoryAccessDeniedError):
            authorize_and_enforce_delegated_context(
                gateway,
                [_Segment("cached", 1)],
                context=_access_context(),
                request=_request(grant_id),
            )
    finally:
        policy.close()


def test_invalid_context_budgets_are_typed() -> None:
    with pytest.raises(DelegatedContextBudgetError) as exc:
        enforce_delegated_context_budget([], max_context_tokens=0)
    assert exc.value.code == "DELEGATED_MEMORY_CONTEXT_BUDGET_INVALID"
    with pytest.raises(DelegatedContextBudgetError):
        enforce_delegated_context_budget([_Segment("bad", -1)], max_context_tokens=1)


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
def test_parent_only_handback_preserves_provenance(
    backend: str, tmp_path: Path
) -> None:
    store = (
        SophiaGraphMemoryStore()
        if backend == "memory"
        else SophiaGraphSqliteStore(tmp_path / "memory.db")
    )
    context = _run_context(memory_grant_id="grant-1")
    proposal = DelegatedMemoryProposal(
        content={"text": "explicit proposal"},
        type="fact",
        namespace=MemoryNamespace(agent_id="parent", project_id="project"),
        workspace_id="workspace",
        proposed_scope="project:project",
        source_record_ids=("source-1",),
    )
    with pytest.raises(PermissionError, match="only the parent"):
        submit_delegated_memory_proposal(
            store,
            proposal,
            run_context=context,
            submitting_parent_agent_id="child",
        )
    candidate_id = submit_delegated_memory_proposal(
        store,
        proposal,
        run_context=context,
        submitting_parent_agent_id="parent",
    )
    loaded = store.get_candidate(candidate_id)
    assert loaded.delegation_provenance.child_agent_id == "child"
    assert loaded.delegation_provenance.source_record_ids == ("source-1",)
    assert store.list_relations(candidate_id) == []


def test_subagent_memory_posture_is_closed_and_cannot_reshare() -> None:
    with pytest.raises(ValueError, match="unsupported memory_posture"):
        _run_context(memory_posture="shared_writable")

    class _Parent:
        name = "child"
        subagent_context = _run_context()

        @staticmethod
        def _ensure_runtime():
            return object()

    with pytest.raises(ValueError, match="cannot be re-shared"):
        subagent(
            _Parent(),  # type: ignore[arg-type]
            name="grandchild",
            memory_posture="read_only_bounded",
            memory_grant_id="copied",
        )


@pytest.mark.parametrize("transport", ["a2a", "mcp"])
def test_transport_mapping_uses_trusted_identity_and_refuses_tokens(
    transport: str,
) -> None:
    payload = {
        "transport": transport,
        "principal_id": "child-principal",
        "audience": "sophiagraph",
        "grant_id": "grant-1",
        "subject_agent_id": "child",
        "parent_run_id": "parent-run",
        "child_run_id": "child-run",
        "trace_parent_id": "trace",
    }
    projection = map_delegated_memory_transport(
        payload,
        trusted_principal_id="child-principal",
        trusted_audience="sophiagraph",
        constraints=(AccessConstraint(mode="deny_all"),),
    )
    assert projection.context.delegated
    assert projection.context.constraints[0].mode == "deny_all"
    with pytest.raises(ConstraintViolationError):
        map_delegated_memory_transport(
            payload,
            trusted_principal_id="other",
            trusted_audience="sophiagraph",
        )
    with pytest.raises(InvalidArgumentError, match="forbidden"):
        map_delegated_memory_transport(
            {**payload, "bearer_token": "secret"},
            trusted_principal_id="child-principal",
            trusted_audience="sophiagraph",
        )


def test_canonical_event_and_telemetry_keep_distinct_sanitized_payloads(
    tmp_path: Path,
) -> None:
    sessions = SQLiteSessionStore(tmp_path / "sessions.db")
    session_id = sessions.create_session(
        initial_agent_id="parent", profile_version="v1"
    )
    try:
        event_id = emit_delegated_memory_session_event(
            sessions,
            session_id=session_id,
            event_type="memory.delegation.access_denied",
            operation="read",
            reason="selector_denied",
            grant_ref="grant-1",
            evidence_refs=("decision:1",),
        )
        event = next(
            item
            for item in sessions.get_events(session_id)
            if item["event_id"] == event_id
        )
        assert "_warnings" not in event["payload"]
        assert "content" not in repr(event["payload"])

        class _Telemetry:
            def __init__(self) -> None:
                self.events = []

            def record_event_sync(self, telemetry_event) -> None:
                self.events.append(telemetry_event)

        telemetry = _Telemetry()
        bridge = DelegatedMemoryTelemetryBridge(telemetry, session_id, "1")
        from sophiagraph.access import MemoryAccessTelemetryEvent

        bridge(
            MemoryAccessTelemetryEvent(
                operation="read",
                outcome="deny",
                reason="selector_denied",
                resolver_outcome="resolved",
                resolver_duration_ms=1.5,
            )
        )
        serialized = repr(telemetry.events[0].data)
        assert "grant-1" not in serialized
        assert "child-principal" not in serialized
        assert telemetry.events[0].data["reason"] == "selector_denied"
    finally:
        sessions.close()


def test_cross_process_revocation_is_seen_on_next_resolution(tmp_path: Path) -> None:
    policy, grant_id = _policy_with_grant(tmp_path)
    try:
        script = (
            "import sys; "
            "from openminion.modules.policy.models import PolicyConfig; "
            "from openminion.modules.policy.runtime.service import PolicyCtl; "
            "policy = PolicyCtl.with_sqlite(sys.argv[1], "
            "config=PolicyConfig(mode='enforce')); "
            "assert policy.revoke_grant(sys.argv[2]); policy.close()"
        )
        completed = run(
            [sys.executable, "-c", script, str(tmp_path / "policy.db"), grant_id],
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
        resolver = OpenMinionDelegationMemoryGrantResolver(
            policy,
            _run_context(memory_grant_id=grant_id),
            memory_scope_namespaces=(_namespace(),),
        )
        assert (
            resolver.resolve_grant(
                grant_id, context=_access_context(), operation="read"
            )
            is None
        )
    finally:
        policy.close()
