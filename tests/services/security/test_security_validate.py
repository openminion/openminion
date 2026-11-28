import stat
from pathlib import Path
from types import SimpleNamespace

from openminion.base.config import OpenMinionConfig
from openminion.modules.policy.diagnostics import security as security_diagnostics
from openminion.services.runtime.plugins import validate_plugin_manifest
from openminion.services.security.validate import run_security_validate
from tests._csc_fixtures import _csc_install_default_agent


def test_service_security_diagnostics_wrap_canonical_report_type() -> None:
    from openminion.modules.policy.diagnostics.security import (
        SecurityValidateReport as canonical,
    )
    from openminion.services.security.validate import (
        SecurityValidateReport as compatibility,
    )

    assert compatibility is canonical


def _base_config() -> OpenMinionConfig:
    config = OpenMinionConfig()
    _csc_install_default_agent(config)  # type: ignore[attr-defined]
    return config


def _run_validate(tmp_path, config: OpenMinionConfig, **kwargs):
    return run_security_validate(
        config=config,
        config_path=tmp_path / "config.json",
        storage_path=tmp_path / "state" / "runtime.db",
        loaded_tool_names=["weather.openmeteo.current"],
        **kwargs,
    )


def test_local_defaults_with_builtin_plugins_are_ok(tmp_path) -> None:
    config = _base_config()
    config.gateway.host = "127.0.0.1"

    report = _run_validate(
        tmp_path,
        config,
        loaded_plugin_manifest_ids=["builtin.validate"],
    )

    assert report.critical_count == 0
    assert report.warn_count == 0
    assert report.status == "ok"


def test_external_gateway_and_open_channel_policy_warn(tmp_path) -> None:
    config = _base_config()
    config.gateway.host = "0.0.0.0"
    config.channel_policy.dm_policy = "open"
    config.channel_policy.group_policy = "open"

    report = _run_validate(
        tmp_path,
        config,
        loaded_plugin_manifest_ids=["builtin.validate"],
    )

    by_id = {item.id: item for item in report.findings}
    assert report.status == "warn"
    assert "gateway.bind_posture" in by_id
    assert by_id["gateway.bind_posture"].severity == "warn"
    assert by_id["channel.dm_policy"].severity == "warn"
    assert by_id["channel.group_policy"].severity == "warn"


def test_inline_secret_and_non_builtin_plugin_warn(tmp_path) -> None:
    config = _base_config()
    config.providers.openai.api_key = "inline-secret"

    report = _run_validate(
        tmp_path,
        config,
        loaded_plugin_manifest_ids=["builtin.validate", "example.extension"],
    )

    by_id = {item.id: item for item in report.findings}
    assert report.status == "warn"
    assert by_id["secrets.redaction_posture"].severity == "warn"
    assert by_id["plugins.trust_posture"].severity == "warn"


def test_restricted_plugin_without_verified_provenance_is_critical(tmp_path) -> None:
    config = _base_config()
    manifest = validate_plugin_manifest(
        {
            "id": "example.restricted",
            "config_schema": {"type": "object"},
            "trust_tier": "restricted",
            "provenance": {"source": "registry", "verified": False},
            "requested_capabilities": ["message.inbound.read"],
        }
    )

    report = _run_validate(
        tmp_path,
        config,
        loaded_plugin_manifest_ids=["example.restricted"],
        loaded_plugin_manifests=[manifest],
    )

    by_id = {item.id: item for item in report.findings}
    assert report.status == "fail"
    assert by_id["plugins.trust_posture"].severity == "critical"


def test_memory_retention_posture_warns_on_extreme_values(tmp_path) -> None:
    config = _base_config()
    config.runtime.memory_log_retention_days = 5000

    report = _run_validate(
        tmp_path,
        config,
        loaded_plugin_manifest_ids=["builtin.validate"],
    )

    by_id = {item.id: item for item in report.findings}
    assert "memory.retention_posture" in by_id
    assert by_id["memory.retention_posture"].severity == "warn"


def test_permission_checks_skip_posix_world_writable_warning_on_windows(
    tmp_path, monkeypatch
) -> None:
    config = _base_config()
    config.gateway.host = "127.0.0.1"
    config_path = tmp_path / "config.json"
    storage_path = tmp_path / "state" / "runtime.db"
    memory_root = tmp_path / "memory"
    config_path.write_text("{}", encoding="utf-8")
    storage_path.parent.mkdir(parents=True, exist_ok=True)
    storage_path.write_text("", encoding="utf-8")
    memory_root.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(
        security_diagnostics, "os", SimpleNamespace(name="nt"), raising=False
    )

    report = security_diagnostics.run_security_validate(
        config=config,
        config_path=config_path,
        storage_path=storage_path,
        memory_root=memory_root,
        loaded_tool_names=["weather.openmeteo.current"],
        loaded_plugin_manifest_ids=["builtin.validate"],
    )

    by_id = {item.id: item for item in report.findings}
    for check_id in (
        "filesystem.config_permissions",
        "filesystem.storage_permissions",
        "filesystem.memory_permissions",
    ):
        assert by_id[check_id].severity == "info"
        assert by_id[check_id].details.get("platform") == "nt"
        msg = by_id[check_id].message.lower()
        assert "not assessed" in msg
        assert "acl safety is not verified" in msg
        assert "ntfs" not in msg
    assert report.warn_count == 0
    assert report.status == "ok"


def test_permission_checks_warn_on_posix_world_writable_path(
    tmp_path, monkeypatch
) -> None:
    config = _base_config()
    config.gateway.host = "127.0.0.1"
    config_path = tmp_path / "config.json"
    config_path.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(
        security_diagnostics, "os", SimpleNamespace(name="posix"), raising=False
    )
    real_stat = Path.stat

    def _fake_stat(self: Path, *args, **kwargs):
        if self == config_path:
            return SimpleNamespace(st_mode=stat.S_IFREG | 0o666)
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", _fake_stat)

    report = _run_validate(
        tmp_path,
        config,
        loaded_plugin_manifest_ids=["builtin.validate"],
    )

    by_id = {item.id: item for item in report.findings}
    finding = by_id["filesystem.config_permissions"]
    assert finding.severity == "warn"
    assert "world-writable" in finding.message.lower()
    assert finding.details.get("mode_octal") == "0o666"


def test_permission_checks_pass_on_posix_restricted_path(tmp_path, monkeypatch) -> None:
    config = _base_config()
    config.gateway.host = "127.0.0.1"
    config_path = tmp_path / "config.json"
    config_path.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(
        security_diagnostics, "os", SimpleNamespace(name="posix"), raising=False
    )
    real_stat = Path.stat

    def _fake_stat(self: Path, *args, **kwargs):
        if self == config_path:
            return SimpleNamespace(st_mode=stat.S_IFREG | 0o600)
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", _fake_stat)

    report = _run_validate(
        tmp_path,
        config,
        loaded_plugin_manifest_ids=["builtin.validate"],
    )

    by_id = {item.id: item for item in report.findings}
    finding = by_id["filesystem.config_permissions"]
    assert finding.severity == "info"
    assert finding.details.get("mode_octal") == "0o600"
    assert report.warn_count == 0
    assert report.status == "ok"
