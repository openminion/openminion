from pathlib import Path

import pytest

from openminion.tools.security.config import SecurityConfig
from openminion.tools.security.process import ScannerProcessResult
from openminion.tools.security.providers import trivy
from openminion.tools.security.schemas import LocalScanArgs

FIXTURES = Path(__file__).parent / "fixtures" / "trivy"


def _config(tmp_path: Path) -> SecurityConfig:
    return SecurityConfig(
        workspace_root=tmp_path,
        allowed_roots=(tmp_path,),
        semgrep_executable="semgrep",
        semgrep_config="rules.yml",
        trivy_executable="trivy",
    )


def _runner(payload: str, *, return_code: int = 1):
    def run(argv, *, cwd, timeout_seconds):
        del cwd, timeout_seconds
        if "--version" in argv:
            return ScannerProcessResult(return_code=0, stdout="Version: 0.70.0\n")
        assert "--skip-db-update" in argv
        assert argv[-1]
        return ScannerProcessResult(return_code=return_code, stdout=payload)

    return run


@pytest.mark.parametrize(
    ("scanner", "args", "fixture", "category", "severity"),
    [
        (
            trivy.scan_dependencies,
            LocalScanArgs(target="."),
            "vulnerability.json",
            "dependency",
            "high",
        ),
        (
            trivy.scan_artifact,
            LocalScanArgs(target="."),
            "misconfiguration.json",
            "misconfiguration",
            "critical",
        ),
        (
            trivy.scan_secrets,
            LocalScanArgs(target="."),
            "secret.json",
            "secret",
            "high",
        ),
    ],
)
def test_trivy_normalizes_supported_local_scans(
    monkeypatch,
    tmp_path: Path,
    scanner,
    args,
    fixture: str,
    category: str,
    severity: str,
) -> None:
    monkeypatch.setattr(trivy.shutil, "which", lambda _name: "/usr/bin/trivy")
    result = scanner(
        args,
        target=tmp_path,
        config=_config(tmp_path),
        process_runner=_runner((FIXTURES / fixture).read_text()),
    )
    assert result.status == "completed"
    assert result.total_findings == 1
    assert result.configuration_identity is not None
    assert result.configuration_identity.scanner_version == "Version: 0.70.0"
    assert "--skip-db-update" in result.configuration_identity.fixed_flags
    assert result.findings[0].category == category
    assert result.findings[0].normalized_severity == severity
    assert "SYNTHETIC_SECRET_VALUE" not in repr(result)


def test_trivy_clean_unavailable_malformed_timeout_and_partial(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(trivy.shutil, "which", lambda _name: None)
    unavailable = trivy.scan_dependencies(
        LocalScanArgs(target="."), target=tmp_path, config=_config(tmp_path)
    )
    assert unavailable.status == "unavailable"
    assert unavailable.configuration_identity is None

    monkeypatch.setattr(trivy.shutil, "which", lambda _name: "/usr/bin/trivy")
    clean = trivy.scan_dependencies(
        LocalScanArgs(target="."),
        target=tmp_path,
        config=_config(tmp_path),
        process_runner=_runner((FIXTURES / "clean.json").read_text(), return_code=0),
    )
    assert clean.status == "completed"
    assert clean.total_findings == 0

    malformed = trivy.scan_dependencies(
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
            return ScannerProcessResult(return_code=0, stdout="Version: 0.70.0")
        return ScannerProcessResult(return_code=124, timed_out=True)

    timed_out = trivy.scan_dependencies(
        LocalScanArgs(target="."),
        target=tmp_path,
        config=_config(tmp_path),
        process_runner=timeout_runner,
    )
    assert timed_out.status == "timed_out"

    partial = trivy.scan_dependencies(
        LocalScanArgs(target="."),
        target=tmp_path,
        config=_config(tmp_path),
        process_runner=_runner(
            (FIXTURES / "vulnerability.json").read_text(), return_code=2
        ),
    )
    assert partial.status == "partial"
    assert partial.total_findings == 1


def test_trivy_version_failure_keeps_fixed_configuration_identity(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(trivy.shutil, "which", lambda _name: "/usr/bin/trivy")

    def version_failure(argv, *, cwd, timeout_seconds):
        del argv, cwd, timeout_seconds
        return ScannerProcessResult(return_code=1, stderr="version failed")

    result = trivy.scan_dependencies(
        LocalScanArgs(target="."),
        target=tmp_path,
        config=_config(tmp_path),
        process_runner=version_failure,
    )

    assert result.status == "unavailable"
    assert result.configuration_identity is not None
    assert result.configuration_identity.scanner_version == ""
    assert result.configuration_identity.scan_mode == "vuln"
    assert result.configuration_identity.fixed_flags == list(trivy.TRIVY_FIXED_FLAGS)
