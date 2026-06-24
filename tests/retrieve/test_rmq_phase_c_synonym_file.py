from __future__ import annotations

from pathlib import Path

from openminion.modules.memory.service import MemoryService
from openminion.modules.memory.storage.memory import InMemoryMemoryStore
from openminion.services.agent.memory.gateway_adapter import MemoryServiceGatewayAdapter
from openminion.services.config import resolve_services_path, resolve_services_roots
from openminion.services.bootstrap.paths import (
    SERVICES_CONFIG_SUBDIR,
)


def test_phase_c_does_not_seed_retrieve_synonym_file(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    monkeypatch.delenv("OPENMINION_DATA_ROOT", raising=False)
    adapter = MemoryServiceGatewayAdapter(
        MemoryService(store=InMemoryMemoryStore()),
        agent_id="seed-check",
        retrieve_ctl=None,
    )
    synonym_path = resolve_services_path(
        Path(SERVICES_CONFIG_SUBDIR) / "retrieve_synonyms.yaml",
        roots=resolve_services_roots(fallback_to_cwd=True),
        relative_to="data_root",
    )

    adapter.build_retrieval_context_with_metadata(
        session_id="seed-check-session",
        user_message="what task should I do next?",
    )
    assert not synonym_path.exists()
