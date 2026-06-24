from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class DbState:
    module_id: str
    db_path: str
    exists: bool
    application_id: int | None
    expected_application_id: int | None
    application_id_matches: bool
    user_version: int
    alembic_revision: str | None
    om_meta: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BackupArtifact:
    module_id: str
    source_db_path: str
    snapshot_path: str
    mode: str
    created_at: str
    user_version: int
    schema_head: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Finding:
    severity: str
    code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class VerificationReport:
    module_id: str
    db_path: str
    level: str
    quick_check: str
    integrity_check: str | None
    findings: list[Finding] = field(default_factory=list)
    ok: bool = True

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["findings"] = [finding.to_dict() for finding in self.findings]
        return payload


@dataclass(frozen=True)
class MigrationReport:
    module_id: str
    db_path: str
    target: str
    before: DbState
    after: DbState
    backup: BackupArtifact
    verification: VerificationReport
    success: bool
    duration_ms: int
    rolled_back: bool = False
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "module_id": self.module_id,
            "db_path": self.db_path,
            "target": self.target,
            "before": self.before.to_dict(),
            "after": self.after.to_dict(),
            "backup": self.backup.to_dict(),
            "verification": self.verification.to_dict(),
            "success": self.success,
            "rolled_back": self.rolled_back,
            "duration_ms": self.duration_ms,
            "error": self.error,
        }


@dataclass(frozen=True)
class RehydrateReport:
    module_id: str
    source_db_path: str
    target_db_path: str
    omx_dir: str
    success: bool
    exported_rows: int = 0
    imported_rows: int = 0
    verification: VerificationReport | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["verification"] = (
            self.verification.to_dict() if self.verification else None
        )
        return payload
