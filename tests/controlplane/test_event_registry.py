from __future__ import annotations

import pytest

from openminion.modules.controlplane.runtime.audit import AuditLogger
from openminion.modules.controlplane.runtime.events import (
    CONTROLPLANE_AUDIT_EVENT_REGISTRY,
    taxonomy_rows,
)


def test_taxonomy_rows_are_deterministic() -> None:
    rows = taxonomy_rows()
    assert rows == taxonomy_rows()
    assert [row["event_type"] for row in rows] == sorted(
        CONTROLPLANE_AUDIT_EVENT_REGISTRY
    )
    assert any(row["event_type"] == "cp.delivery.failed" for row in rows)


def test_schema_validation_rejects_unknown_event_only_when_enabled() -> None:
    AuditLogger(schema_validation_enabled=False).emit("cp.future.not_registered")
    with pytest.raises(ValueError, match="Unregistered controlplane audit event"):
        AuditLogger(schema_validation_enabled=True).emit("cp.future.not_registered")


def test_schema_validation_accepts_registered_event() -> None:
    logger = AuditLogger(schema_validation_enabled=True)
    event = logger.emit("cp.delivery.sent", details={"channel": "telegram"})
    assert event.event_type == "cp.delivery.sent"
