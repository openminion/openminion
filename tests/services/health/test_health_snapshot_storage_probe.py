from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock

import pytest

from openminion.base.config import OpenMinionConfig
from openminion.modules.storage.runtime.context import build_runtime_storage
from openminion.services.health import probes
from openminion.services.health.service import collect_health_snapshot
from tests._csc_fixtures import _csc_install_default_agent


class _Names:
    def names(self) -> list[str]:
        return []


def _make_echo_config(tmp_path: Path) -> OpenMinionConfig:
    config = OpenMinionConfig()
    _csc_install_default_agent(config)
    config.runtime.log_level = "ERROR"
    _csc_install_default_agent(config, provider="echo")
    config.storage.path = str(tmp_path / "state" / "health.db")
    return config


def _build_live_runtime(
    config: OpenMinionConfig,
    tmp_path: Path,
    runtime_storage: Any = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        config=config,
        config_path=tmp_path / "config.json",
        home_root=tmp_path,
        config_manager=None,
        channels=_Names(),
        plugins=_Names(),
        provider=SimpleNamespace(name="echo"),
        runtime_storage=runtime_storage,
    )


def test_live_runtime_repeated_snapshots_close_call_owned_probe_storage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _make_echo_config(tmp_path)
    # The runtime's own (borrowed) storage engine must survive the snapshot.
    runtime_storage = build_runtime_storage(config.storage.path)
    runtime = _build_live_runtime(config, tmp_path, runtime_storage=runtime_storage)

    probe_contexts: list[Any] = []
    real_build = probes.build_runtime_storage

    def tracking_build(*args: Any, **kwargs: Any) -> Any:
        context = real_build(*args, **kwargs)
        probe_contexts.append(context)
        return context

    monkeypatch.setattr(probes, "build_runtime_storage", tracking_build)

    try:
        for _ in range(3):
            collect_health_snapshot(config_path=None, runtime=runtime)  # type: ignore[arg-type]

        assert len(probe_contexts) == 3
        assert all(context._closed for context in probe_contexts)  # noqa: SLF001
        # The borrowed live-runtime storage is preserved, not closed by the probe.
        assert runtime_storage._closed is False  # noqa: SLF001
    finally:
        runtime_storage.close()


def test_live_runtime_snapshot_closes_probe_when_failure_after_acquisition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _make_echo_config(tmp_path)
    runtime = _build_live_runtime(config, tmp_path)

    probe_contexts: list[Any] = []
    real_build = probes.build_runtime_storage

    def tracking_build(*args: Any, **kwargs: Any) -> Any:
        context = real_build(*args, **kwargs)
        probe_contexts.append(context)
        return context

    monkeypatch.setattr(probes, "build_runtime_storage", tracking_build)

    import openminion.services.health.service as health_service

    monkeypatch.setattr(
        health_service,
        "_build_health_payload",
        mock.Mock(side_effect=RuntimeError("payload boom")),
    )

    with pytest.raises(RuntimeError, match="payload boom"):
        collect_health_snapshot(config_path=None, runtime=runtime)  # type: ignore[arg-type]

    assert len(probe_contexts) == 1
    assert probe_contexts[0]._closed  # noqa: SLF001
