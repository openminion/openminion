from __future__ import annotations

from pathlib import Path

from openminion.modules.tool.authoring.schemas import AuthoredToolAuditEventRow
from openminion.modules.tool.authoring.storage import (
    SQLiteToolAuthoringAuditSink,
    default_tool_authoring_audit_db_path,
    encode_audit_details,
)


def test_default_tool_authoring_audit_db_path_uses_sibling_audit_sqlite() -> None:
    store_path = Path("/tmp/openminion/authored_tools/store.sqlite")

    assert default_tool_authoring_audit_db_path(store_path) == Path(
        "/tmp/openminion/authored_tools/audit.sqlite"
    )


def test_authored_tool_audit_sink_round_trips_all_required_event_types(
    tmp_path: Path,
) -> None:
    sink = SQLiteToolAuthoringAuditSink(tmp_path / "authored_tools" / "audit.sqlite")
    event_types = [
        "tool_authoring.drafted",
        "tool_authoring.inspected",
        "tool_authoring.registered",
        "tool_authoring.invoked",
        "tool_authoring.promoted",
        "tool_authoring.force_promoted",
        "tool_authoring.scope_changed",
        "tool_authoring.removed",
        "policy.grant_issued",
        "policy.grant_revoked",
    ]
    expected: list[AuthoredToolAuditEventRow] = []

    try:
        for idx, event_type in enumerate(event_types, start=1):
            event = AuthoredToolAuditEventRow(
                event_id=f"event-{idx}",
                timestamp=f"2026-05-20T00:00:{idx:02d}+00:00",
                event_type=event_type,
                target_kind="tool" if "grant" not in event_type else "policy_grant",
                target_id=f"target-{idx}",
                agent_id="agent-1",
                session_id="session-1",
                version_hash=f"sha256:{idx:02d}",
                details_json=encode_audit_details(
                    {"event_type": event_type, "ordinal": idx}
                ),
            )
            sink.append_event(event)
            expected.append(event)

        assert sink.list_events() == expected
        assert sink.db_path.name == "audit.sqlite"
    finally:
        sink.close()
