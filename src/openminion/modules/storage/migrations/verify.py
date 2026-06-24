from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from openminion.modules.storage.migrations.errors import VerificationError
from openminion.modules.storage.migrations.models import Finding, VerificationReport

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection as SAConnection
    from sqlalchemy.engine import Engine

    VerificationConnection = sqlite3.Connection | SAConnection
else:
    VerificationConnection = Any
    Engine = Any

VerifierHook = Callable[[VerificationConnection], list[Finding]]


def _run_pragma(conn: sqlite3.Connection, pragma_name: str) -> str:
    row = conn.execute(f"PRAGMA {pragma_name}").fetchone()
    if row is None or row[0] is None:
        return "unknown"
    return str(row[0])


def run_verification(
    *,
    module_id: str,
    db_path: Path,
    level: str = "quick",
    verifier_hook: VerifierHook | None = None,
    raise_on_fatal: bool = False,
    engine: Engine | None = None,
    connection: VerificationConnection | None = None,
) -> VerificationReport:
    normalized = str(level or "quick").strip().lower()
    if normalized not in {"quick", "full"}:
        normalized = "quick"

    db_path = db_path.expanduser().resolve(strict=False)
    findings: list[Finding] = []
    integrity_result: str | None = None

    if connection is not None:
        if engine is not None:
            raise ValueError("pass either engine or connection, not both")
        try:
            from sqlalchemy import text
        except Exception:
            text = None  # type: ignore[assignment]

        if hasattr(connection, "exec_driver_sql"):
            result = connection.execute(text("SELECT 1 AS ok")).scalar()  # type: ignore[union-attr]
            quick_result = "ok" if int(result or 0) == 1 else "error"
            if quick_result != "ok":
                findings.append(
                    Finding(
                        severity="fatal",
                        code="connectivity_check_failed",
                        message="Postgres connectivity check returned non-ok result.",
                        details={"result": quick_result},
                    )
                )
        else:
            quick_result = _run_pragma(connection, "quick_check")  # type: ignore[arg-type]
            if quick_result != "ok":
                findings.append(
                    Finding(
                        severity="fatal",
                        code="quick_check_failed",
                        message="PRAGMA quick_check returned non-ok result.",
                        details={"result": quick_result},
                    )
                )
            if normalized == "full":
                integrity_result = _run_pragma(connection, "integrity_check")  # type: ignore[arg-type]
                if integrity_result != "ok":
                    findings.append(
                        Finding(
                            severity="fatal",
                            code="integrity_check_failed",
                            message="PRAGMA integrity_check returned non-ok result.",
                            details={"result": integrity_result},
                        )
                    )

        if verifier_hook is not None:
            hook_findings = verifier_hook(connection)
            for finding in hook_findings:
                findings.append(finding)
    elif engine is not None:
        from sqlalchemy import text

        with engine.connect() as conn:
            result = conn.execute(text("SELECT 1 AS ok")).scalar()
            quick_result = "ok" if int(result or 0) == 1 else "error"
            if quick_result != "ok":
                findings.append(
                    Finding(
                        severity="fatal",
                        code="connectivity_check_failed",
                        message="Postgres connectivity check returned non-ok result.",
                        details={"result": quick_result},
                    )
                )
            if verifier_hook is not None:
                hook_findings = verifier_hook(conn)
                for finding in hook_findings:
                    findings.append(finding)
    else:
        with sqlite3.connect(str(db_path)) as conn:
            quick_result = _run_pragma(conn, "quick_check")

            if quick_result != "ok":
                findings.append(
                    Finding(
                        severity="fatal",
                        code="quick_check_failed",
                        message="PRAGMA quick_check returned non-ok result.",
                        details={"result": quick_result},
                    )
                )

            if normalized == "full":
                integrity_result = _run_pragma(conn, "integrity_check")
                if integrity_result != "ok":
                    findings.append(
                        Finding(
                            severity="fatal",
                            code="integrity_check_failed",
                            message="PRAGMA integrity_check returned non-ok result.",
                            details={"result": integrity_result},
                        )
                    )

            if verifier_hook is not None:
                hook_findings = verifier_hook(conn)
                for finding in hook_findings:
                    findings.append(finding)

    fatal_count = sum(1 for finding in findings if finding.severity == "fatal")
    report = VerificationReport(
        module_id=module_id,
        db_path=str(db_path),
        level=normalized,
        quick_check=quick_result,
        integrity_check=integrity_result,
        findings=findings,
        ok=fatal_count == 0,
    )

    if raise_on_fatal and not report.ok:
        first_fatal = next(
            (finding for finding in report.findings if finding.severity == "fatal"),
            None,
        )
        message = (
            first_fatal.message
            if first_fatal
            else "Verification failed with fatal findings."
        )
        raise VerificationError(message)

    return report
