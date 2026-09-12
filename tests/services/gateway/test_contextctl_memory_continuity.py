from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import os
from types import SimpleNamespace
from unittest.mock import Mock, patch

from openminion.base.constants import STATE_KEY_ACTIVE
from openminion.modules.context.memory_client import (
    build_mid_session_recall_query,
    select_recent_session_artifacts,
)
from openminion.modules.memory.models import MemoryRecord
from openminion.modules.memory.service import MemoryService
from openminion.modules.memory.storage.memory import InMemoryMemoryStore
from openminion.services.agent.memory.gateway_adapter import (
    MemoryServiceGatewayAdapter,
)

from tests.services.gateway._gateway_service_support import (
    GatewayServiceTestCase,
    _CaptureProvider,
)


def test_mid_session_query_uses_only_user_text_and_typed_state() -> None:
    query = build_mid_session_recall_query(
        latest_user_message="continue the repair",
        intent_ids=["intent-a"],
        intent_statuses=["active"],
        active_skill_id="python",
        resolved_skill_ids=["python"],
        plan_cursor=2,
        plan_step_ids=["step-a"],
        recent_tool_families=["file"],
    )

    assert query == "continue the repair intent-a active python cursor-2 step-a file"


def test_recent_artifact_selection_rejects_stale_expired_and_current_records() -> None:
    now = datetime(2026, 9, 12, tzinfo=timezone.utc)

    def record(
        record_id: str,
        *,
        session_id: str = "prior-session",
        updated_at: str | None = None,
        expires_at: str | None = None,
    ) -> SimpleNamespace:
        return SimpleNamespace(
            id=record_id,
            content={
                "session_id": session_id,
                "artifact_type": "file",
                "artifact_path": f"/workspace/{record_id}.py",
                "artifact_digest": f"sha256:{record_id}",
                "turn_index": 3,
                "tool_name": "file.write",
            },
            meta={},
            evidence_refs=[],
            updated_at=updated_at,
            expires_at=expires_at,
        )

    selected = select_recent_session_artifacts(
        [
            record("eligible", updated_at=now.isoformat()),
            record(
                "expired",
                updated_at=now.isoformat(),
                expires_at=(now - timedelta(seconds=1)).isoformat(),
            ),
            record("stale", updated_at=(now - timedelta(days=15)).isoformat()),
            record(
                "current",
                session_id="current-session",
                updated_at=now.isoformat(),
            ),
            record("malformed", updated_at="not-a-time"),
        ],
        current_session_id="current-session",
        max_results=5,
        max_session_age=14,
        now=now,
    )

    assert [item.record_id for item in selected] == ["eligible"]


class ContextCtlGatewayContinuityTests(GatewayServiceTestCase):
    def test_real_gateway_retains_memory_manifest_across_typed_state_change(
        self,
    ) -> None:
        memory_service = MemoryService(store=InMemoryMemoryStore())
        now = datetime.now(timezone.utc).isoformat()
        project_memory_id = memory_service._store.put(  # noqa: SLF001
            MemoryRecord(
                id="project-memory",
                scope="project:project-a",
                type="project_convention",
                title="Project convention",
                content={"text": "Release notes use sentence case."},
                confidence=0.9,
                created_at=now,
                updated_at=now,
            )
        )
        global_memory_id = memory_service._store.put(  # noqa: SLF001
            MemoryRecord(
                id="global-memory",
                scope="global:system",
                type="user_preference",
                title="Global preference",
                content={"text": "Prefer compact progress updates."},
                confidence=0.9,
                created_at=now,
                updated_at=now,
            )
        )
        query_fact_id = memory_service._store.put(  # noqa: SLF001
            MemoryRecord(
                id="query-fact",
                scope="agent:main",
                type="fact",
                title="Query marker",
                content={"text": "The cobalt marker is enabled."},
                confidence=0.9,
                created_at=now,
                updated_at=now,
            )
        )
        first_turn_id = memory_service._store.put(  # noqa: SLF001
            MemoryRecord(
                id="first-turn-memory",
                scope="agent:main",
                type="session_summary",
                title="Prior session",
                content={
                    "summary_text": "The unrelated ember decision remains active."
                },
                created_at=now,
                updated_at=now,
            )
        )
        artifact_id = memory_service._store.put(  # noqa: SLF001
            MemoryRecord(
                id="prior-artifact",
                scope="project:project-a",
                type="artifact_digest",
                title="Prior artifact",
                content={
                    "session_id": "prior-session",
                    "artifact_type": "file",
                    "artifact_path": "/workspace/report.md",
                    "artifact_digest": "sha256:report",
                    "turn_index": 4,
                    "tool_name": "file.write",
                },
                created_at=now,
                updated_at=now,
            )
        )
        mid_session_id = memory_service._store.put(  # noqa: SLF001
            MemoryRecord(
                id="mid-session-memory",
                scope="agent:main",
                type="fact",
                title="Typed-state marker",
                content={"text": "Continue. intent-alpha active cursor-1 repair-step"},
                confidence=0.9,
                created_at=now,
                updated_at=now,
            )
        )
        unrelated_id = memory_service._store.put(  # noqa: SLF001
            MemoryRecord(
                id="unrelated-memory",
                scope="agent:other",
                type="user_preference",
                title="Other agent preference",
                content={"text": "Other agents prefer verbose progress updates."},
                confidence=0.9,
                created_at=now,
                updated_at=now,
            )
        )
        memory_gateway = MemoryServiceGatewayAdapter(
            memory_service,
            agent_id="main",
            project_id="project-a",
        )
        provider = _CaptureProvider()
        with patch.dict(
            os.environ,
            {
                "CONTEXTCTL_GATEWAY_ENABLED": "true",
                "OPENMINION_IDENTITY_ROOT": str(self.database_path.parent / "identity"),
            },
            clear=False,
        ):
            gateway, _sink = self._build_gateway(
                provider=provider,
                logger_name="openminion.tests.contextctl-continuity",
                agent_logger_name="openminion.tests.contextctl-continuity.agent",
                agent_memory=memory_gateway,
            )
            try:
                first = asyncio.run(
                    gateway.run_once(
                        channel="console",
                        target="contextctl-user",
                        message="cobalt",
                        inbound_metadata={"attach_id": "contextctl-continuity"},
                    )
                )
                session_id = first.metadata["session_id"]
                service = gateway._contextctl_adapter._service  # noqa: SLF001
                mid_session_recall = Mock(
                    wraps=service._memctl.recall_mid_session_memory  # noqa: SLF001
                )
                service._memctl.recall_mid_session_memory = (  # noqa: SLF001
                    mid_session_recall
                )
                first_manifest = service._latest_manifest_by_session[  # noqa: SLF001
                    session_id
                ]
                first_ids = {
                    *first_manifest.facts,
                    *first_manifest.memory,
                    *first_manifest.session_start_recalled_memory,
                    *first_manifest.recent_session_artifacts,
                }
                assert {
                    project_memory_id,
                    global_memory_id,
                    query_fact_id,
                    first_turn_id,
                    artifact_id,
                }.issubset(first_ids), first_manifest.model_dump()
                assert query_fact_id in first_manifest.facts
                assert {
                    project_memory_id,
                    global_memory_id,
                    first_turn_id,
                }.issubset(first_manifest.session_start_recalled_memory)
                assert first_manifest.decision_trace is not None
                first_trace_refs = {
                    ref
                    for decision in first_manifest.decision_trace.decisions
                    for ref in decision.refs
                }
                assert {
                    project_memory_id,
                    global_memory_id,
                    query_fact_id,
                    first_turn_id,
                    artifact_id,
                }.issubset(first_trace_refs)
                assert unrelated_id not in first_ids
                assert unrelated_id not in first_trace_refs

                self.sessions.update_session_metadata(
                    session_id=session_id,
                    patch={
                        STATE_KEY_ACTIVE: {
                            "intent_execution_states": [
                                {"intent_id": "intent-alpha", "status": "active"}
                            ],
                            "cursor": 1,
                            "plan": {"steps": [{"command_id": "repair-step"}]},
                        }
                    },
                )
                second = asyncio.run(
                    gateway.run_once(
                        channel="console",
                        target="contextctl-user",
                        message="Continue.",
                        inbound_metadata={"attach_id": "contextctl-continuity"},
                    )
                )
                assert second.metadata["session_id"] == session_id
                assert gateway._contextctl_adapter._service is service  # noqa: SLF001
                second_manifest = service._latest_manifest_by_session[  # noqa: SLF001
                    session_id
                ]
                second_ids = {
                    *second_manifest.facts,
                    *second_manifest.memory,
                    *second_manifest.mid_session_recalled_memory,
                }
                assert mid_session_id in second_ids
                mid_session_recall.assert_called_once()
                assert mid_session_recall.call_args.kwargs["intent_ids"] == [
                    "intent-alpha"
                ]
                assert second_manifest.mid_session_recall_state is not None
                assert (
                    second_manifest.mid_session_recall_state.intent_states[0].intent_id
                    == "intent-alpha"
                )
                assert second_manifest.decision_trace is not None
                second_trace_refs = {
                    ref
                    for decision in second_manifest.decision_trace.decisions
                    for ref in decision.refs
                }
                assert mid_session_id in second_trace_refs
                assert unrelated_id not in second_trace_refs
            finally:
                gateway.close()
                memory_service.close()
