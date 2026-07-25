from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from openminion.cli.commands import sidecar as sidecar_cli
from openminion.tools.browser.providers.pinchtab.binary import (
    PINCHTAB_ALLOW_EXTERNAL_ENV,
    PINCHTAB_BIN_ENV,
    PINCHTAB_DOWNLOAD_URL_ENV,
    PINCHTAB_E2E_ENV,
    PINCHTAB_INSTALL_MODE_ENV,
    PINCHTAB_SHA256_ENV,
    PINCHTAB_VERSION_ENV,
    PinchTabBinaryConfig,
    PinchTabBinaryError,
    PinchTabBinaryResolver,
    PinchTabReleaseAsset,
)


def _write_cached_binary(data_root: Path, *, version: str = "v1") -> Path:
    binary = (
        data_root
        / "sidecars"
        / "pinchtab"
        / "releases"
        / version
        / "test-os"
        / "pinchtab"
    )
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    digest = hashlib.sha256(binary.read_bytes()).hexdigest()
    (binary.parent / "pinchtab.sha256").write_text(
        f"{digest}  pinchtab\n", encoding="utf-8"
    )
    (binary.parent / "manifest.json").write_text(
        json.dumps({"version": version, "platform": "test-os", "verified": True}),
        encoding="utf-8",
    )
    return binary


def _cfg(data_root: Path, **overrides: object) -> PinchTabBinaryConfig:
    payload = {
        "data_root": data_root,
        "install_mode": "never",
        "version": "v1",
        "source": "github_release",
        "sha256": "",
        "platform_id": "test-os",
        "allow_external": False,
        "explicit_path": "",
        "download_url": "",
    }
    payload.update(overrides)
    return PinchTabBinaryConfig(**payload)


def test_resolver_returns_cached_binary_with_configured_checksum(
    tmp_path: Path,
) -> None:
    binary = _write_cached_binary(tmp_path)
    expected = hashlib.sha256(binary.read_bytes()).hexdigest()

    result = PinchTabBinaryResolver(_cfg(tmp_path, sha256=expected)).resolve()

    assert result.ok is True
    assert result.binary_path == binary
    assert result.managed is True
    assert result.verified is True
    assert result.manifest_path == binary.parent / "manifest.json"


def test_cached_checksum_file_does_not_make_untrusted_binary_verified(
    tmp_path: Path,
) -> None:
    binary = _write_cached_binary(tmp_path)

    result = PinchTabBinaryResolver(_cfg(tmp_path)).resolve()

    assert result.ok is True
    assert result.binary_path == binary
    assert result.verified is False


def test_resolver_rejects_external_explicit_path_without_allow_external(
    tmp_path: Path,
) -> None:
    outside = tmp_path.parent / "pinchtab-external"
    outside.write_text("x", encoding="utf-8")

    resolver = PinchTabBinaryResolver(_cfg(tmp_path, explicit_path=str(outside)))

    with pytest.raises(
        PinchTabBinaryError, match="pinchtab_binary must be under data_root"
    ):
        resolver.resolve()


def test_resolver_allows_external_path_when_explicitly_enabled(tmp_path: Path) -> None:
    outside = tmp_path.parent / "pinchtab-external-ok"
    outside.write_text("x", encoding="utf-8")

    result = PinchTabBinaryResolver(
        _cfg(tmp_path, explicit_path=str(outside), allow_external=True)
    ).resolve()

    assert result.ok is True
    assert result.legacy is True
    assert result.managed is False


def test_resolver_download_uses_injected_downloader_and_verifies_checksum(
    tmp_path: Path,
) -> None:
    source_bytes = b"pinchtab-test"
    expected = hashlib.sha256(source_bytes).hexdigest()
    events: list[str] = []

    def downloader(_url: str, destination: Path) -> None:
        destination.write_bytes(source_bytes)

    resolver = PinchTabBinaryResolver(
        _cfg(
            tmp_path,
            install_mode="required",
            download_url="https://example.invalid/pinchtab",
            sha256=expected,
        ),
        downloader=downloader,
        event_sink=lambda event, _payload: events.append(event),
    )

    result = resolver.resolve(allow_download=True)

    assert result.ok is True
    assert result.verified is True
    assert events == [
        "sidecar.pinchtab.download.requested",
        "sidecar.pinchtab.download.completed",
    ]


def test_resolver_discovers_github_release_asset_with_injected_discoverer(
    tmp_path: Path,
) -> None:
    source_bytes = b"pinchtab-discovered"
    expected = hashlib.sha256(source_bytes).hexdigest()
    downloaded_urls: list[str] = []

    def downloader(url: str, destination: Path) -> None:
        downloaded_urls.append(url)
        destination.write_bytes(source_bytes)

    resolver = PinchTabBinaryResolver(
        _cfg(tmp_path, install_mode="required", download_url=""),
        downloader=downloader,
        release_discoverer=lambda _cfg: PinchTabReleaseAsset(
            download_url="https://example.invalid/releases/pinchtab",
            sha256=expected,
            resolved_version="v1.2.3",
        ),
    )

    result = resolver.resolve(allow_download=True)

    assert result.ok is True
    assert result.verified is True
    assert downloaded_urls == ["https://example.invalid/releases/pinchtab"]


def test_resolver_rejects_real_download_without_e2e_opt_in(tmp_path: Path) -> None:
    resolver = PinchTabBinaryResolver(
        _cfg(
            tmp_path,
            install_mode="required",
            download_url="https://example.invalid/pinchtab",
        )
    )

    with pytest.raises(PinchTabBinaryError) as exc_info:
        resolver.resolve(allow_download=True)

    assert exc_info.value.code == "PINCHTAB_DOWNLOAD_NOT_APPROVED"


def test_resolver_can_load_from_environment(tmp_path: Path) -> None:
    env = {
        PINCHTAB_VERSION_ENV: "v9",
        PINCHTAB_INSTALL_MODE_ENV: "required",
        PINCHTAB_ALLOW_EXTERNAL_ENV: "1",
        PINCHTAB_BIN_ENV: str(tmp_path / "bin"),
        PINCHTAB_DOWNLOAD_URL_ENV: "https://example.invalid/pinchtab",
        PINCHTAB_SHA256_ENV: "abc",
        PINCHTAB_E2E_ENV: "0",
    }

    cfg = PinchTabBinaryConfig.from_env(data_root=tmp_path, runtime_env=env)

    assert cfg.version == "v9"
    assert cfg.install_mode == "required"
    assert cfg.allow_external is True
    assert cfg.explicit_path == str(tmp_path / "bin")
    assert cfg.download_url == "https://example.invalid/pinchtab"
    assert cfg.sha256 == "abc"


def test_sidecar_pinchtab_status_cli_reports_binary_status(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_cached_binary(tmp_path)
    config = SimpleNamespace(
        runtime=SimpleNamespace(env={"OPENMINION_PINCHTAB_PLATFORM": "test-os"}),
        security=_security_config(),
    )
    monkeypatch.setenv("OPENMINION_DATA_ROOT", str(tmp_path))
    monkeypatch.setattr(sidecar_cli, "load_cli_config_from_args", lambda _args: config)

    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    sidecar_cli.register(subparsers)
    args = parser.parse_args(
        ["sidecar", "pinchtab", "status", "--version", "v1", "--json"]
    )
    args.config = ""

    assert sidecar_cli.run_sidecar(args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["binary"]["ok"] is True
    assert payload["binary"]["managed"] is True


def _security_config() -> SimpleNamespace:
    return SimpleNamespace(
        tool_policy=SimpleNamespace(
            max_calls_per_run=100,
            max_calls_per_tool=50,
            max_budget_cost_per_run=1000,
            default_required_scopes=[],
        )
    )
