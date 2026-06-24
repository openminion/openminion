from __future__ import annotations

import ast
from pathlib import Path

from sophiagraph import (
    MemoryNamespace,
    SophiaGraphMemoryStore,
    build_okf_navigation_packet,
    export_okf_bundle,
    import_okf_bundle,
    import_okf_bundle_into_store,
)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _namespace() -> MemoryNamespace:
    return MemoryNamespace(tenant_id="tenant", agent_id="openminion", graph_id="main")


def test_openminion_can_consume_public_okf_bundle_surface(tmp_path: Path) -> None:
    root = tmp_path / "okf"
    _write(
        root / "index.md",
        "# Index\n\n- [Roadmap](/Roadmap.md)\n- [Decision Log](/Decision Log.md)\n",
    )
    _write(
        root / "Roadmap.md",
        """---
type: concept
title: Roadmap
aliases: [Plan]
---
# Roadmap

See [[Decision Log|decisions]].

Decision Log appears as plain text here.
""",
    )
    _write(
        root / "Decision Log.md",
        """---
type: decision
title: Decision Log
---
# Decision Log

Back to [Roadmap](/Roadmap.md).
""",
    )

    bundle = import_okf_bundle(root, namespace=_namespace())
    packet = build_okf_navigation_packet(bundle, current_path="Roadmap.md")
    exported = export_okf_bundle(bundle, obsidian_compatible=True)
    store = SophiaGraphMemoryStore()
    imported = import_okf_bundle_into_store(
        store,
        root,
        namespace=_namespace(),
        scope="agent:openminion",
        vault_id="okf-openminion",
    )

    assert bundle.manifest.concept_count == 2
    assert packet.document_kind == "concept"
    assert {link.source_path for link in packet.backlinks} == {
        "Decision Log.md",
        "index.md",
    }
    assert len(packet.unlinked_mentions) == 1
    assert any(
        "[[Decision Log.md|decisions]]" in payload.content for payload in exported
    )
    assert imported.created_count == 3


def test_openminion_okf_fixture_uses_public_sophiagraph_imports_only() -> None:
    source = Path(__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    forbidden = {
        "sophiagraph.okf",
        "sophiagraph.models.okf",
        "sophiagraph.vault",
        "sophiagraph.storage.memory",
        "sophiagraph.storage.sqlite",
    }
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name)
    leaked = imports & forbidden
    assert not leaked, (
        f"fixture reaches into private SophiaGraph paths: {sorted(leaked)}"
    )
