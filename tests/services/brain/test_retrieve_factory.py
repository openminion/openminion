from __future__ import annotations

from openminion.modules.memory.service import MemoryService
from openminion.modules.memory.storage.memory import InMemoryMemoryStore
from openminion.modules.retrieve.config import load_config as load_retrieve_config
from openminion.services.agent.memory.gateway_adapter import MemoryServiceGatewayAdapter
from openminion.services.brain.factory.retrieve import (
    build_retrieve_service,
    init_retrieve_adapter,
)


class DummyLogger:
    def info(self, *args, **kwargs) -> None:
        pass

    def warning(self, *args, **kwargs) -> None:
        pass


def test_build_retrieve_service_returns_retrievectl(tmp_path) -> None:
    cfg = load_retrieve_config(home_root=tmp_path)
    service = build_retrieve_service(
        home_root=tmp_path,
        vector_adapter=None,
        config=cfg,
        logger=DummyLogger(),
    )
    try:
        assert service is not None
    finally:
        if service is not None:
            service.close()


def test_retrieve_factory_shares_single_service_instance(tmp_path) -> None:
    cfg = load_retrieve_config(home_root=tmp_path)
    service = build_retrieve_service(
        home_root=tmp_path,
        vector_adapter=None,
        config=cfg,
        logger=DummyLogger(),
    )
    assert service is not None
    try:
        retrieve_api = init_retrieve_adapter(
            mode="auto",
            home_root=tmp_path,
            vector_adapter=None,
            config=cfg,
            logger=DummyLogger(),
            retrieve_service=service,
        )
        adapter = MemoryServiceGatewayAdapter(
            MemoryService(InMemoryMemoryStore()),
            agent_id="bridge-test",
            retrieve_ctl=service,
        )
        assert retrieve_api is not None
        assert id(getattr(retrieve_api, "_svc", None)) == id(service)
        assert id(getattr(adapter, "_retrieve_ctl", None)) == id(service)
    finally:
        service.close()
