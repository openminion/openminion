from pathlib import Path

import pytest

from openminion.tools.security.config import SecurityConfig
from openminion.tools.security.process import ScannerProcessResult
from openminion.tools.security.providers import semgrep
from openminion.tools.security.schemas import LocalScanArgs

FIXTURES = Path(__file__).parent / "fixtures" / "semgrep"


def _config(tmp_path: Path) -> SecurityConfig:
    return SecurityConfig(
        workspace_root=tmp_path,
        allowed_roots=(tmp_path,),
        semgrep_executable="semgrep",
        semgrep_config=str(FIXTURES / "rule.yml"),
        trivy_executable="trivy",
    )


def _runner(payload: str, *, return_code: int = 1):
    def run(argv, *, cwd, timeout_seconds):
        del cwd, timeout_seconds
        if "--version" in argv:
            return ScannerProcessResult(return_code=0, stdout="1.140.0\n")
        return ScannerProcessResult(return_code=return_code, stdout=payload)

    return run


@pytest.mark.parametrize(
    ("fixture", "exit_code", "status", "count"),
    [("finding.json", 1, "completed", 1), ("clean.json", 0, "completed", 0)],
)
def test_semgrep_normalizes_clean_and_finding_results(
    monkeypatch, tmp_path: Path, fixture: str, exit_code: int, status: str, count: int
) -> None:
    monkeypatch.setattr(semgrep.shutil, "which", lambda _name: "/usr/bin/semgrep")
    result = semgrep.scan_code(
        LocalScanArgs(target="."),
        target=tmp_path,
        config=_config(tmp_path),
        process_runner=_runner((FIXTURES / fixture).read_text(), return_code=exit_code),
    )
    assert result.status == status
    assert result.total_findings == count
    assert result.scanner_version == "1.140.0"
    assert result.configuration_identity is not None
    assert result.configuration_identity.scanner_version == "1.140.0"
    assert result.configuration_identity.scan_mode == "code"
    assert len(result.configuration_identity.config_sha256) == 64
    if result.findings:
        finding = result.findings[0]
        assert finding.rule_id == "openminion.test.shell-true"
        assert finding.normalized_severity == "high"
        assert "subprocess.run" not in repr(result)


def test_semgrep_unavailable_malformed_timeout_and_partial(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(semgrep.shutil, "which", lambda _name: None)
    unavailable = semgrep.scan_code(
        LocalScanArgs(target="."), target=tmp_path, config=_config(tmp_path)
    )
    assert unavailable.status == "unavailable"
    assert unavailable.configuration_identity is None

    monkeypatch.setattr(semgrep.shutil, "which", lambda _name: "/usr/bin/semgrep")
    malformed = semgrep.scan_code(
        LocalScanArgs(target="."),
        target=tmp_path,
        config=_config(tmp_path),
        process_runner=_runner((FIXTURES / "malformed.json").read_text()),
    )
    assert malformed.status == "failed"
    assert malformed.error and malformed.error.code == "INVALID_RESPONSE"

    def timeout_runner(argv, *, cwd, timeout_seconds):
        del cwd, timeout_seconds
        if "--version" in argv:
            return ScannerProcessResult(return_code=0, stdout="1.140.0")
        return ScannerProcessResult(return_code=124, timed_out=True)

    timed_out = semgrep.scan_code(
        LocalScanArgs(target="."),
        target=tmp_path,
        config=_config(tmp_path),
        process_runner=timeout_runner,
    )
    assert timed_out.status == "timed_out"

    finding_payload = (
        (FIXTURES / "finding.json")
        .read_text()
        .replace('"errors": []', '"errors": [{"message": "partial"}]')
    )
    partial = semgrep.scan_code(
        LocalScanArgs(target="."),
        target=tmp_path,
        config=_config(tmp_path),
        process_runner=_runner(finding_payload, return_code=2),
    )
    assert partial.status == "partial"
    assert partial.total_findings == 1


def test_semgrep_version_failure_keeps_local_config_identity(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(semgrep.shutil, "which", lambda _name: "/usr/bin/semgrep")

    def version_failure(argv, *, cwd, timeout_seconds):
        del argv, cwd, timeout_seconds
        return ScannerProcessResult(return_code=1, stderr="version failed")

    result = semgrep.scan_code(
        LocalScanArgs(target="."),
        target=tmp_path,
        config=_config(tmp_path),
        process_runner=version_failure,
    )

    assert result.status == "unavailable"
    assert result.configuration_identity is not None
    assert result.configuration_identity.scanner_version == ""
    assert result.configuration_identity.scan_mode == "code"
    assert len(result.configuration_identity.config_sha256) == 64
