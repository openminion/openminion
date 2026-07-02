from __future__ import annotations

import ast
from pathlib import Path

from sophiagraph import (
    CandidateListOptions,
    CandidateQueueOptions,
    KnowledgeExplorerRequest,
    ListQueryOptions,
    MemoryCandidate,
    MemoryNamespace,
    MemoryRecord,
    PublishProfile,
    SophiaGraphMemoryStore,
    WorkbenchActionRequest,
    WorkspaceWorkbenchRequest,
    build_publish_plan,
    build_workbench_review_inbox,
    build_workspace_workbench_packet,
    list_candidate_queue,
    preview_workbench_action,
    publish_overlay_from_plan,
    workbench_to_dict,
)
from sophiagraph.ui import (
    build_candidate_review_screen,
    build_explorer_screen,
    build_record_detail_screen,
    render_collaborative_workbench_html,
)


def _namespace() -> MemoryNamespace:
    return MemoryNamespace(agent_id="openminion", graph_id="main")


def test_openminion_can_assemble_collaborative_workbench_packet() -> None:
    store = SophiaGraphMemoryStore()
    store.put_record(
        MemoryRecord(
            id="rec-1",
            scope="agent:openminion",
            type="fact",
            key="rec-1",
            title="Operator Note",
            content={"text": "Explicit operator note."},
            namespace=_namespace(),
            created_at="2026-07-01T00:00:00+00:00",
            updated_at="2026-07-01T00:00:00+00:00",
        )
    )
    store.put_candidate(
        MemoryCandidate(
            candidate_id="candidate-1",
            session_id="session-1",
            proposed_scope="agent:openminion",
            type="fact",
            title="Candidate Note",
            content={"text": "Needs approval."},
            source="agent_inferred",
            status="proposed",
            namespace=_namespace(),
            claim_key="operator.note",
            polarity="asserts",
            source_class="user_input",
            created_at="2026-07-01T00:01:00+00:00",
            updated_at="2026-07-01T00:01:00+00:00",
        )
    )
    profile = PublishProfile(profile_id="local-share", kind="read_only_share")
    plan = build_publish_plan(
        profile,
        store.list_records(ListQueryOptions(scopes=["agent:openminion"])),
    )
    preview = preview_workbench_action(
        WorkbenchActionRequest(
            action="propose_note_edit",
            target_id="rec-1",
            actor_id="agent",
            workspace_id="workspace",
            requires_review=True,
        ),
        publish_plan=plan,
    )
    packet = build_workspace_workbench_packet(
        WorkspaceWorkbenchRequest(
            workspace_id="workspace",
            actor_id="agent",
            root_record_id="rec-1",
            query="operator",
        ),
        explorer=build_explorer_screen(
            store,
            KnowledgeExplorerRequest(
                scopes=["agent:openminion"],
                namespaces=[_namespace()],
                query="operator",
                root_record_id="rec-1",
            ),
        ),
        record_detail=build_record_detail_screen(store, record_id="rec-1"),
        candidate_review=build_candidate_review_screen(
            store,
            CandidateListOptions(status="proposed"),
        ),
        review_inbox=build_workbench_review_inbox(
            candidates=tuple(
                list_candidate_queue(store, CandidateQueueOptions(status="proposed"))
            )
        ),
        publish=publish_overlay_from_plan(plan),
        action_previews=(preview,),
    )

    assert workbench_to_dict(packet)["state"]["workspace_id"] == "workspace"
    assert packet.review_inbox.pending_count == 1
    assert "workspace Workbench" in render_collaborative_workbench_html(packet)


def test_openminion_fixture_uses_public_sophiagraph_imports_only() -> None:
    source = Path(__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert "sophiagraph.storage" not in modules
    assert not any(module.startswith("sophiagraph._") for module in modules)
    assert modules
