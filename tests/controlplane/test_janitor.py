from __future__ import annotations

from openminion.modules.controlplane.runtime.audit import AuditLogger
from openminion.modules.controlplane.runtime.janitor import (
    ControlPlaneJanitor,
    ControlPlaneJanitorSidecar,
    ControlPlaneRetentionPolicy,
)


class _FakeStore:
    def __init__(self) -> None:
        self.deleted: list[str] = []
        self.counts = {
            "cp_audit_events": 3,
            "cp_outbox": 2,
            "cp_pair_tokens": 1,
            "cp_pair_attempts": 4,
            "cp_rate_limits": 5,
            "cp_wizard_sessions": 6,
        }

    def _execute_count(self, sql: str, _params: tuple[object, ...]) -> int:
        table = sql.split(" FROM ", 1)[1].split(" WHERE ", 1)[0].strip()
        self.deleted.append(table)
        return self.counts.get(table, 0)

    def _query_dicts(self, sql: str, _params: tuple[object, ...]) -> list[dict[str, int]]:
        table = sql.split(" FROM ", 1)[1].split(" WHERE ", 1)[0].strip()
        return [{"count": self.counts.get(table, 0)}]


def test_janitor_deletes_each_retention_table_and_emits_audit() -> None:
    store = _FakeStore()
    audit = AuditLogger()
    result = ControlPlaneJanitor(
        store=store,
        policy=ControlPlaneRetentionPolicy(),
        audit_logger=audit,
    ).run_once()

    assert set(result.deleted) == set(store.counts)
    assert store.deleted == list(store.counts)
    assert audit.events[-1].event_type == "cp.janitor.cycle.completed"
    assert audit.events[-1].details["deleted"] == result.deleted


def test_janitor_dry_run_counts_without_deleting() -> None:
    store = _FakeStore()
    result = ControlPlaneJanitor(store=store, dry_run=True).run_once()

    assert result.dry_run is True
    assert result.deleted["cp_audit_events"] == 3
    assert store.deleted == []


def test_janitor_sidecar_runs_one_cycle_and_stops() -> None:
    sidecar = ControlPlaneJanitorSidecar(
        janitor=ControlPlaneJanitor(store=_FakeStore()),
        interval_seconds=1,
        run_once=True,
    )
    status = sidecar.start()
    sidecar.stop(kill=True)

    assert status["ok"] is True
    assert sidecar.status()["last_result"] is not None
