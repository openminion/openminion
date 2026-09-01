from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from cryptography.fernet import Fernet

from openminion.base.config import OpenMinionConfig
from openminion.modules.brain.adapters.factory import tool as tool_factory
from openminion.modules.brain.adapters.tool.runtime import ToolAdapter
from openminion.modules.secret.service import SecretService
from openminion.services.brain.factory import adapter as service_factory
from openminion.services.runtime.bootstrap import build_secret_service


def test_secret_sync_and_async_reads_share_the_same_value(tmp_path: Path) -> None:
    service = SecretService(
        str(tmp_path / "secret.db"),
        Fernet.generate_key().decode(),
    )
    asyncio.run(service.set_secret("signer", "private-value", namespace="chain"))

    assert service.get_secret_sync("signer", namespace="chain") == "private-value"
    assert (
        asyncio.run(service.get_secret("signer", namespace="chain")) == "private-value"
    )
    service.close_sync()


def test_secret_close_sync_is_idempotent(tmp_path: Path) -> None:
    service = SecretService(
        str(tmp_path / "secret.db"),
        Fernet.generate_key().decode(),
    )
    close = Mock(wraps=service._store.close)
    service._store.close = close

    service.close_sync()
    service.close_sync()
    asyncio.run(service.close())

    close.assert_called_once_with()


def test_bootstrap_builds_no_service_without_master_key(tmp_path: Path) -> None:
    config = OpenMinionConfig.from_dict({"runtime": {"env": {}}})

    assert build_secret_service(config=config, data_root=tmp_path) is None


def test_bootstrap_builds_service_at_canonical_data_path(tmp_path: Path) -> None:
    key = Fernet.generate_key().decode()
    config = OpenMinionConfig.from_dict(
        {"runtime": {"env": {"OPENMINION_SECRET_KEY": key}}}
    )

    service = build_secret_service(config=config, data_root=tmp_path)

    assert isinstance(service, SecretService)
    assert service._db_path == str(tmp_path / "secret" / "secrets.db")
    service.close_sync()


def test_module_factory_forwards_identical_secret_service(monkeypatch) -> None:
    sentinel = object()
    captured: dict[str, object] = {}

    class _Adapter:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(
        "openminion.modules.brain.adapters.tool.ToolAdapter",
        _Adapter,
    )

    result = tool_factory.create_tool_adapter(
        mode="strict",
        secret_service=sentinel,
    )

    assert isinstance(result, _Adapter)
    assert captured["secret_service"] is sentinel


def test_service_factory_forwards_identical_secret_service(monkeypatch) -> None:
    sentinel = object()
    captured: dict[str, object] = {}

    def _create_tool_adapter(**kwargs: object) -> object:
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(service_factory, "create_tool_adapter", _create_tool_adapter)

    service_factory.create_tool_api(
        mode="strict",
        workspace_root=".",
        runtime_config=SimpleNamespace(reactions_enabled=True),
        secret_service=sentinel,
    )

    assert captured["secret_service"] is sentinel


def test_tool_adapter_closes_injected_service_once(tmp_path: Path) -> None:
    secret_service = SimpleNamespace(close_sync=Mock())
    adapter = ToolAdapter(
        workspace_root=tmp_path,
        secret_service=secret_service,
        artifactctl=SimpleNamespace(close=Mock()),
    )

    adapter.close()
    adapter.close()

    secret_service.close_sync.assert_called_once_with()
