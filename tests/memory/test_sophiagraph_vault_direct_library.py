from __future__ import annotations

from sophiagraph import (
    MemoryNamespace,
    SophiaGraphMemoryStore,
    VaultExportOptions,
    VaultFilePayload,
    VaultImportOptions,
    export_vault_files,
    import_vault_files,
)


def test_openminion_can_submit_explicit_vault_note_payload_to_sophiagraph() -> None:
    namespace = MemoryNamespace(
        tenant_id="openminion",
        agent_id="agent",
        graph_id="vault-fixture",
    )
    store = SophiaGraphMemoryStore()
    note = """---
tags: [openminion]
---
# Run Note

Model-authored note with [[Next Step]].
"""

    result = import_vault_files(
        store,
        [VaultFilePayload(path="Notes/Run Note.md", content=note)],
        VaultImportOptions(
            vault_id="openminion-fixture",
            namespace=namespace,
            scope="agent:agent",
            root_label="openminion",
            imported_at="2026-05-25T00:00:00+00:00",
        ),
    )
    exported = export_vault_files(
        store,
        VaultExportOptions(
            vault_id="openminion-fixture",
            namespace=namespace,
            scope="agent:agent",
        ),
    )

    assert result.created_count == 1
    assert result.manifest.files[0].path == "Notes/Run Note.md"
    assert exported.files[0].content == note
    assert import_vault_files.__module__ == "sophiagraph.vault"
