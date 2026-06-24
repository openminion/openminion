from pathlib import Path
import sqlite3
from typing import Any

from openminion.modules.storage.migrations.verify import run_verification


def _resolve(path: str | Path) -> Path:
    return Path(path).expanduser().resolve(strict=False)


def _report_to_dict(report: Any) -> dict[str, Any]:
    """Serialize storage reports through their explicit model contract."""
    if hasattr(report, "to_dict"):
        return report.to_dict()
    if hasattr(report, "model_dump"):
        return report.model_dump(mode="json")
    return dict(report)


def verify_module_integrity(module_id: str, db_path: str | Path) -> dict[str, Any]:
    """Run integrity checks for a module store."""
    report = run_verification(module_id=module_id, db_path=_resolve(db_path))
    return _report_to_dict(report)


def repair_module_db(module_id: str, db_path: str | Path) -> dict[str, Any]:
    """Attempt light repairs (checkpoint + VACUUM) for a module store."""
    path = _resolve(db_path)
    actions: list[str] = []
    try:
        with sqlite3.connect(str(path)) as conn:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            actions.append("wal_checkpoint")
            conn.execute("VACUUM")
            actions.append("vacuum")
            conn.commit()
        return {"ok": True, "actions": actions}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "actions": actions, "error": str(exc)}
