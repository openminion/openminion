from __future__ import annotations

from pathlib import Path

from openminion.modules.storage.migrations.models import VerificationReport
from openminion.modules.storage.runtime import module_integrity


def test_verify_module_integrity_serializes_dataclass_report(
    monkeypatch, tmp_path: Path
):

    def fake_run_verification(*, module_id: str, db_path: Path) -> VerificationReport:
        return VerificationReport(
            module_id=module_id,
            db_path=str(db_path),
            level="quick",
            quick_check="ok",
            integrity_check=None,
            ok=True,
        )

    monkeypatch.setattr(module_integrity, "run_verification", fake_run_verification)

    payload = module_integrity.verify_module_integrity(
        "session", tmp_path / "db.sqlite"
    )

    assert payload["module_id"] == "session"
    assert payload["quick_check"] == "ok"
    assert payload["findings"] == []


def test_verify_module_integrity_still_accepts_model_dump_reports(
    monkeypatch, tmp_path: Path
):

    class PydanticStyleReport:
        def model_dump(self, *, mode: str) -> dict[str, str]:
            return {"mode": mode, "module_id": "session"}

    def fake_run_verification(*, module_id: str, db_path: Path) -> PydanticStyleReport:
        del module_id, db_path
        return PydanticStyleReport()

    monkeypatch.setattr(module_integrity, "run_verification", fake_run_verification)

    payload = module_integrity.verify_module_integrity(
        "session", tmp_path / "db.sqlite"
    )

    assert payload == {"mode": "json", "module_id": "session"}
