from __future__ import annotations

from sophiagraph import (
    ConnectorReplayRequest,
    FreshnessLedgerEntry,
    FreshnessReindexRequest,
    LocalSyncRequest,
    MemoryBlock,
    MemoryNamespace,
    RepairFollowUpRequest,
    SharedBlockAttachment,
    SourceIngestEnvelope,
    SourceRegistryEntry,
    SummaryContextRequest,
    SophiaGraphMemoryStore,
    SharedBlockUsageEvent,
    SyncRunRequest,
    assemble_entity_summary_context,
    build_inspection_report,
    execute_operational_run,
    decide_source_ingest,
    detect_sync_conflict,
)
from sophiagraph.models import EntityFactProvenance, EntitySummary


def _ns(agent_id: str = "agent") -> MemoryNamespace:
    return MemoryNamespace(tenant_id="openminion", agent_id=agent_id, graph_id="main")


def test_openminion_direct_library_sync_freshness_and_connector_paths() -> None:
    store = SophiaGraphMemoryStore()
    conflict = detect_sync_conflict(
        LocalSyncRequest(
            mode="file_primary",
            namespace=_ns(),
            source_id="vault:openminion",
            path="Notes/A.md",
            previous_file_hash="h1",
            previous_record_hash="r1",
            current_file_hash="h2",
            current_record_hash="r2",
        ),
        observed_at="2026-05-31T00:00:00+00:00",
    ).conflict
    assert conflict is not None
    store.put_sync_conflict(conflict)

    source = SourceRegistryEntry(
        source_id="connector:fake",
        source_type="test_fake",
        namespace=_ns(),
        display_name="Fake connector",
        permission_scope="read_only",
    )
    envelope = SourceIngestEnvelope.create(
        source_id=source.source_id,
        namespace=source.namespace,
        payload_kind="document",
        payload={"id": "doc-1"},
        cursor="cursor-1",
        content_hash="hash-1",
    )
    store.put_source_entry(source)
    store.put_source_ingest(envelope)
    freshness = FreshnessLedgerEntry.create(
        namespace=_ns(),
        source_kind="openminion_submission",
        source_id="submission:1",
        status="fresh",
        cursor="cursor-1",
        content_hash="hash-1",
    )
    store.put_freshness_entry(freshness)

    assert store.list_sync_conflicts(status="open") == [conflict]
    assert decide_source_ingest(source, envelope).accepted
    assert store.list_freshness_entries(source_kind="openminion_submission") == [
        freshness
    ]


def test_openminion_direct_library_shared_block_and_inspection_paths() -> None:
    store = SophiaGraphMemoryStore()
    block = MemoryBlock(
        block_id="block-1",
        class_name="agent_identity",
        mode="read_only",
        content="Identity policy",
        token_estimate=2,
        owner_namespace=_ns("owner"),
        source="operator",
        created_at="2026-05-31T00:00:00+00:00",
        last_updated_at="2026-05-31T00:00:00+00:00",
    )
    attachment = SharedBlockAttachment.create(
        block_id=block.block_id,
        namespace=_ns("agent"),
        attached_agent_id="agent",
        attached_at="2026-05-31T00:01:00+00:00",
    )
    usage = SharedBlockUsageEvent(
        event_id="usage-1",
        block_id=block.block_id,
        namespace=_ns("agent"),
        agent_id="agent",
        action="read",
        occurred_at="2026-05-31T00:02:00+00:00",
    )
    store.put_memory_block(block)
    store.put_shared_block_attachment(attachment)
    store.put_shared_block_usage_event(usage)

    report = build_inspection_report(
        report_id="report-1",
        namespace=_ns(),
        generated_at="2026-05-31T00:00:00+00:00",
        records=[],
    )

    assert store.list_shared_block_attachments(block_id=block.block_id) == [attachment]
    assert store.list_shared_block_usage_events(action="read") == [usage]
    assert report.findings == []


def test_openminion_direct_library_operational_envelope_paths() -> None:
    source = SourceRegistryEntry(
        source_id="connector:fake",
        source_type="test_fake",
        namespace=_ns(),
        display_name="Fake connector",
        permission_scope="read_only",
    )
    envelope = SourceIngestEnvelope.create(
        source_id=source.source_id,
        namespace=source.namespace,
        payload_kind="document",
        payload={"id": "doc-1"},
        cursor="cursor-2",
        content_hash="hash-2",
    )
    freshness = FreshnessLedgerEntry.create(
        namespace=_ns(),
        source_kind="connector",
        source_id=source.source_id,
        status="fresh",
        cursor="cursor-1",
        content_hash="hash-1",
    )
    sync_request = LocalSyncRequest(
        mode="file_primary",
        namespace=_ns(),
        source_id="vault:openminion",
        path="Notes/A.md",
        previous_file_hash="h1",
        previous_record_hash="r1",
        current_file_hash="h2",
        current_record_hash="r2",
    )
    sync_report = execute_operational_run(
        SyncRunRequest(
            run_id="sync-1",
            sync_request=sync_request,
            observed_at="2026-06-04T00:00:00+00:00",
        )
    )
    replay_report = execute_operational_run(
        ConnectorReplayRequest(
            run_id="replay-1",
            source=source,
            envelope=envelope,
            existing_freshness=freshness,
            updated_at="2026-06-04T00:01:00+00:00",
        )
    )
    reindex_report = execute_operational_run(
        FreshnessReindexRequest(
            run_id="reindex-1",
            namespace=_ns(),
            source_kind="connector",
            source_id=source.source_id,
            existing_freshness=freshness,
            force_rebuild=True,
        )
    )
    repair_report = execute_operational_run(
        RepairFollowUpRequest(
            run_id="repair-1",
            namespace=_ns(),
            generated_at="2026-06-04T00:02:00+00:00",
            records=[],
        )
    )

    assert sync_report.kind == "sync_run"
    assert sync_report.status == "follow_up_required"
    assert replay_report.kind == "connector_replay"
    assert replay_report.status == "accepted"
    assert reindex_report.kind == "freshness_reindex"
    assert reindex_report.status == "rebuild_required"
    assert repair_report.kind == "repair_followup"
    assert repair_report.status == "unchanged"


def test_openminion_direct_library_entity_summary_context_path() -> None:
    store = SophiaGraphMemoryStore()
    summary = EntitySummary(
        summary_id="sum-1",
        entity_id="entity-1",
        namespace=_ns(),
        summary_text="Caller supplied entity summary",
        provenance=EntityFactProvenance(
            source_kind="tool_observation",
            source_id="tool-1",
            actor="agent",
        ),
        source_record_ids=("rec-1",),
        created_at="2026-06-15T00:00:00+00:00",
        updated_at="2026-06-15T00:00:00+00:00",
    )
    store.put_entity_summary(summary)

    result = assemble_entity_summary_context(
        store,
        SummaryContextRequest(summary_ids=["sum-1"]),
        source_owner="openminion",
    )

    assert [item.summary_id for item in result.items] == ["sum-1"]
    assert result.items[0].summary_text == "Caller supplied entity summary"
    assert result.omitted == []
