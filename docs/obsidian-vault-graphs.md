# Obsidian Vault Graphs

Status: alpha
Last updated: 2026-09-06

OpenMinion can use one Markdown folder as both an Obsidian vault and a
SophiaGraph document graph. Obsidian is the human editing surface;
SophiaGraph indexes vault links and text; PragmaGraph independently indexes
repository structure. OpenMinion composes both through configured graph
sources. Durable agent memory remains a separate backend.

## Configure the sources

Start from `test-configs/per-agent-minimax-obsidian-vault-graph-template.json`.
Copy it to a local file and replace the absolute-path placeholders:

- `workspace_root`: the initialized SophiaGraph workspace.
- `source_root`: the Markdown folder opened as an Obsidian vault.
- `snapshot_path`: an optional PragmaGraph repository snapshot.

The names in `active` are **Sources**. Their `provider` fields name the
**Adapters**. Each Source belongs to the external `provider` **Layer** and has
structured tags such as `document_graph` or `code_graph`.

The template reads the MiniMax credential from `MINIMAX_API_KEY`; it contains
no credential. Durable memory uses its separate supported SophiaGraph backend;
vault refresh does not promote note content into memory. SophiaGraph and
PragmaGraph roots are config-owned and cannot be overridden by a model tool
call or CLI graph operation.

## Use the vault

Write or edit Markdown with Obsidian or OpenMinion's existing `file.*` tools.
Then explicitly refresh the configured vault graph:

```bash
openminion --config /absolute/path/to/local-profile.json \
  graph refresh --provider vault_graph
```

Query and follow links without refreshing implicitly:

```bash
openminion --config /absolute/path/to/local-profile.json \
  graph query "project marker" --provider vault_graph
openminion --config /absolute/path/to/local-profile.json \
  graph neighborhood RECORD_ID --provider vault_graph
```

Inside Focus, `/graph` prints the equivalent copyable commands and points to
`openminion graph status` for configured Source/Adapter mappings and viewer
readiness. The model uses the generic `graph.query`, `graph.neighborhood`, and
confirmed `graph.refresh` tools. Every operation targets exactly one Source.

Query repository relationships separately:

```bash
openminion --config /absolute/path/to/local-profile.json \
  graph query "runtime owner" --provider repo_graph
```

Results retain their Source, Layer, tags, and citations; they are not merged
into unattributed prose by the CLI.

## Native Obsidian validation

Obsidian's desktop CLI must be enabled in Obsidian under
**Settings > General > Advanced > Command line interface** before native
search, links, or backlinks commands can validate the vault. OpenMinion does
not enable this setting, expose the native CLI to the model, or substitute raw
file search for a native Obsidian result.

## Boundaries

- `file.*` writes Markdown files.
- `graph.refresh` explicitly syncs an already configured graph Source.
- SophiaGraph owns vault indexing, search, and link neighborhoods.
- PragmaGraph owns repository/code relationships.
- The memory backend alone stores durable agent memories.
- No watcher, automatic refresh, automatic memory promotion, retry, or hidden
  provider fallback is added by this integration.

For visual inspection rather than graph operations, see
[Visual Graph Viewer](graph-viewer.md).
