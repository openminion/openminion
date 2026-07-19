from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from openminion.base.config import OpenMinionConfig
from openminion.services.agent.memory.gateway_adapter import MemoryServiceGatewayAdapter
from openminion.services.runtime import bootstrap
from openminion.services.runtime.plugins import PluginManifest
from openminion.services.runtime.errors import (
    PluginActivationError,
    RuntimeBootstrapError,
)


def test_runtime_bootstrap_errors_preserve_runtime_error_compatibility() -> None:
    assert issubclass(RuntimeBootstrapError, RuntimeError)
    assert issubclass(PluginActivationError, RuntimeBootstrapError)


def test_plugin_trust_denial_raises_domain_error(monkeypatch) -> None:
    monkeypatch.setattr(
        bootstrap,
        "evaluate_plugin_trust_policy",
        lambda **kwargs: SimpleNamespace(
            decision="deny",
            reason_code="test-denial",
        ),
    )
    manifest = PluginManifest(
        id="sample",
        name="Sample",
        version="0.0.1",
        description="",
        config_schema={"type": "object"},
        trust_tier="local_dev",
        requested_capabilities=(),
        provenance_source="local_path",
        provenance_uri="",
        provenance_publisher="",
        provenance_checksum="",
        provenance_verified=False,
    )

    with pytest.raises(PluginActivationError, match="test-denial"):
        bootstrap.enforce_plugin_activation_policy(
            security_policy=SimpleNamespace(policy_version="test"),
            agent_id="agent",
            manifest=manifest,
        )


def test_identity_seed_profile_failures_are_observable(caplog) -> None:
    class BrokenIdentityCtl:
        def get_profile(self, agent_id: str):
            raise LookupError(agent_id)

        def load_profile(self, agent_id: str):
            raise OSError(agent_id)

    with caplog.at_level(logging.DEBUG):
        bootstrap._try_seed_identity(
            memory_adapter=object(),
            identity_ctl=BrokenIdentityCtl(),
            agent_id="agent",
            logger=logging.getLogger("test.bootstrap.identity"),
        )

    assert "get_profile failed" in caplog.text
    assert "load_profile failed" in caplog.text


def test_summary_structurer_failure_is_observable(
    monkeypatch, caplog, tmp_path
) -> None:
    adapter = object.__new__(MemoryServiceGatewayAdapter)
    adapter.configure_session_summary_structurer = lambda value: None

    def fail_structurer():
        raise RuntimeError("structurer unavailable")

    monkeypatch.setattr(bootstrap, "GatewayService", lambda *args, **kwargs: object())
    logger = logging.getLogger("test.bootstrap.summary")

    with caplog.at_level(logging.WARNING):
        result = bootstrap.build_gateway_service(
            agent_service=SimpleNamespace(
                build_session_summary_structurer=fail_structurer
            ),
            profile_name="agent",
            config=OpenMinionConfig(),
            channels=object(),
            sessions=object(),
            idempotency=object(),
            security_policy=object(),
            channel_authenticity_policy=object(),
            config_path=tmp_path / "config.json",
            storage_path=tmp_path / "runtime.db",
            memory_root=tmp_path / "memory",
            home_root=tmp_path / "home",
            data_root=tmp_path / "data",
            logger=logger,
            session_context=object(),
            agent_memory=adapter,
        )

    assert result is not None
    assert "session summary structurer unavailable" in caplog.text
