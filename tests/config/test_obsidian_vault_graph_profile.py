from __future__ import annotations

import json
from pathlib import Path

from openminion.base.config import OpenMinionConfig
from openminion.modules.context.knowledge import resolve_knowledge_graphs_config
from openminion.modules.memory.backends.config import resolve_backend_config


def test_obsidian_vault_graph_profile_uses_supported_backends() -> None:
    package_root = Path(__file__).resolve().parents[2]
    profile_path = (
        package_root
        / "examples"
        / "configs"
        / "per-agent-minimax-obsidian-vault-graph-template.json"
    )
    config = OpenMinionConfig.from_dict(
        json.loads(profile_path.read_text(encoding="utf-8"))
    )
    graphs = resolve_knowledge_graphs_config(config)

    assert (
        resolve_backend_config(config.module_configs["memory"]).provider
        == "sophiagraph"
    )
    assert graphs.provider.active == ("vault_graph", "repo_graph")
    assert graphs.provider.providers["vault_graph"].provider == "sophiagraph_workspace"
    assert graphs.provider.providers["repo_graph"].provider == "pragmagraph"
