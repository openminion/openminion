from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

from openminion.base.config import OpenMinionConfig
from openminion.modules.memory.backends.external import (
    register_reference_sqlite_backend,
)
from openminion.modules.memory.errors import InvalidArgumentError
from openminion.services.agent.memory.gateway_adapter import (
    DisabledMemoryGatewayAdapter,
)
from openminion.services.agent.memory.gateway_adapter import MemoryServiceGatewayAdapter
from openminion.services.runtime.bootstrap import build_agent_memory_service
from openminion.services.runtime.bootstrap import build_knowledge_graph_source_service
from tests._csc_fixtures import _csc_install_default_agent


_PRAGMAGRAPH_SRC = Path(__file__).resolve().parents[2].parent / "pragmagraph" / "src"
if str(_PRAGMAGRAPH_SRC) not in sys.path:
    sys.path.insert(0, str(_PRAGMAGRAPH_SRC))


def _clear_pragmagraph_modules() -> None:
    for name in tuple(sys.modules):
        if name == "pragmagraph" or name.startswith("pragmagraph."):
            sys.modules.pop(name, None)


def _build_config() -> OpenMinionConfig:
    config = OpenMinionConfig()
    _csc_install_default_agent(config)  # type: ignore[attr-defined]
    config.runtime.memory_enabled = True
    config.runtime.memory_provider = "memory_v2"
    return config


def test_build_agent_memory_service_retrieve_ctl_none(tmp_path) -> None:
    adapter = build_agent_memory_service(
        config=_build_config(),
        agent_id="di-agent",
        memory_root=tmp_path,
        logger=logging.getLogger("di.none"),
        retrieve_ctl=None,
    )
    assert isinstance(adapter, MemoryServiceGatewayAdapter)
    assert getattr(adapter, "_retrieve_ctl", None) is None
    assert getattr(adapter, "_candidate_learning_config", None) is not None


def test_build_agent_memory_service_retrieve_ctl_passthrough(tmp_path) -> None:
    marker = object()
    adapter = build_agent_memory_service(
        config=_build_config(),
        agent_id="di-agent",
        memory_root=tmp_path,
        logger=logging.getLogger("di.mock"),
        retrieve_ctl=marker,
    )
    assert isinstance(adapter, MemoryServiceGatewayAdapter)
    assert getattr(adapter, "_retrieve_ctl", None) is marker


def test_build_agent_memory_service_keeps_runtime_and_memory_summary_budgets_separate(
    tmp_path,
) -> None:
    config = _build_config()
    config.runtime.session_summary_max_chars = 4321

    adapter = build_agent_memory_service(
        config=config,
        agent_id="di-agent",
        memory_root=tmp_path,
        logger=logging.getLogger("di.summary_budgets"),
        retrieve_ctl=None,
    )

    assert isinstance(adapter, MemoryServiceGatewayAdapter)
    assert getattr(adapter, "_session_summary_max_chars", None) == 500


def test_build_agent_memory_service_tolerates_adapter_signature_drift(
    monkeypatch,
    tmp_path,
) -> None:
    from openminion.services.runtime import bootstrap as runtime_bootstrap

    class _CompatAdapter:
        def __init__(
            self,
            service,
            *,
            agent_id: str,
            retrieve_ctl=None,
            ranking_config=None,
            **kwargs,
        ) -> None:
            self._service = service
            self._agent_id = agent_id
            self._retrieve_ctl = retrieve_ctl
            self._ranking_config = ranking_config

    monkeypatch.setattr(
        runtime_bootstrap, "MemoryServiceGatewayAdapter", _CompatAdapter
    )

    adapter = runtime_bootstrap.build_agent_memory_service(
        config=_build_config(),
        agent_id="di-agent",
        memory_root=tmp_path,
        logger=logging.getLogger("di.compat"),
        retrieve_ctl=None,
    )

    assert isinstance(adapter, _CompatAdapter)
    assert getattr(adapter, "_retrieve_ctl", "missing") is None


def test_build_agent_memory_service_supports_none_backend(
    monkeypatch,
    tmp_path,
) -> None:
    from openminion.services.runtime import bootstrap as runtime_bootstrap

    monkeypatch.setattr(
        runtime_bootstrap,
        "_resolve_bootstrap_memory_config",
        lambda **kwargs: {"backend": {"provider": "none"}},
    )

    adapter = runtime_bootstrap.build_agent_memory_service(
        config=_build_config(),
        agent_id="di-none",
        memory_root=tmp_path,
        logger=logging.getLogger("di.none_backend"),
        retrieve_ctl=None,
    )

    assert isinstance(adapter, DisabledMemoryGatewayAdapter)
    assert adapter.enabled is False


def test_build_agent_memory_service_rejects_unimplemented_external_backend(
    monkeypatch,
    tmp_path,
) -> None:
    from openminion.services.runtime import bootstrap as runtime_bootstrap

    monkeypatch.setattr(
        runtime_bootstrap,
        "_resolve_bootstrap_memory_config",
        lambda **kwargs: {
            "backend": {
                "provider": "external",
                "external_adapter": "not_registered",
            }
        },
    )

    with pytest.raises(InvalidArgumentError, match="No external backend registered"):
        runtime_bootstrap.build_agent_memory_service(
            config=_build_config(),
            agent_id="di-external",
            memory_root=tmp_path,
            logger=logging.getLogger("di.external_backend"),
            retrieve_ctl=None,
        )


def test_build_agent_memory_service_supports_reference_sqlite_external_backend(
    monkeypatch,
    tmp_path,
) -> None:
    from openminion.services.runtime import bootstrap as runtime_bootstrap

    register_reference_sqlite_backend()
    external_db = tmp_path / "reference.sqlite3"
    monkeypatch.setattr(
        runtime_bootstrap,
        "_resolve_bootstrap_memory_config",
        lambda **kwargs: {
            "backend": {
                "provider": "external",
                "external_adapter": "reference-sqlite",
                "options": {"db_path": str(external_db)},
            }
        },
    )

    adapter = runtime_bootstrap.build_agent_memory_service(
        config=_build_config(),
        agent_id="di-external-ok",
        memory_root=tmp_path,
        logger=logging.getLogger("di.external_backend_ok"),
        retrieve_ctl=None,
    )

    assert isinstance(adapter, MemoryServiceGatewayAdapter)


def test_build_knowledge_graph_source_service_wires_graphify(tmp_path) -> None:
    graph_path = tmp_path / "graph.json"
    graph_path.write_text(
        '{"nodes": [{"id": "repo", "label": "Repo graph", "path": "README.md"}]}',
        encoding="utf-8",
    )
    config = _build_config()
    config.module_configs["knowledge_graphs"] = {
        "provider": {
            "active": ["repo_graph"],
            "providers": {
                "repo_graph": {
                    "provider": "graphify",
                    "graph_path": str(graph_path),
                    "required_capabilities": ["query", "citations", "provenance"],
                }
            },
        }
    }

    service = build_knowledge_graph_source_service(config=config)

    assert [source.name for source in service.list_sources()] == ["repo_graph"]


def test_build_knowledge_graph_source_service_wires_pragmagraph(tmp_path) -> None:
    _clear_pragmagraph_modules()
    from pragmagraph.adapters import index_path
    from pragmagraph.storage import save_snapshot

    fixture = (
        Path(__file__).resolve().parents[2].parent
        / "pragmagraph"
        / "fixtures"
        / "tiny_repo"
    )
    snapshot_path = tmp_path / "pragma.json"
    save_snapshot(index_path(fixture, namespace="fixture"), snapshot_path)
    config = _build_config()
    config.module_configs["knowledge_graphs"] = {
        "provider": {
            "active": ["repo_pragmas"],
            "providers": {
                "repo_pragmas": {
                    "provider": "pragmagraph",
                    "snapshot_path": str(snapshot_path),
                    "required_capabilities": ["query", "citations", "provenance"],
                }
            },
        }
    }

    service = build_knowledge_graph_source_service(config=config)

    assert [source.name for source in service.list_sources()] == ["repo_pragmas"]
