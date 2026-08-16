# Graph Viewer Example

This example opens OpenMinion's visual graph lens over a small third-brain
provider envelope. It is intentionally static: the envelope is a checked-in
sample of the shape a document, code, or artifact graph provider can expose to
GraphFakos.

From the package root, copy the two fixtures into an isolated runtime home so
the example does not write state into the tracked examples tree:

```bash
graph_home="$(mktemp -d)"
cp examples/graph-viewer/agents.json examples/graph-viewer/repo-viewer-envelope.json "$graph_home/"

python -m openminion \
  --home-root "$graph_home" \
  --config "$graph_home/agents.json" \
  graph status

python -m openminion \
  --home-root "$graph_home" \
  --config "$graph_home/agents.json" \
  graph view --brain third --provider repo_graph
```

For a no-browser proof:

```bash
python -m openminion \
  --home-root "$graph_home" \
  --config "$graph_home/agents.json" \
  graph view --brain third --provider repo_graph --html-out "$graph_home/viewer.html"
```

For host-app integration, start with the JSON probe:

```bash
python -m openminion \
  --home-root "$graph_home" \
  --config "$graph_home/agents.json" \
  graph view --brain third --provider repo_graph --dry-run --json
```

Remove `$graph_home` when you are done.

The viewer is a lens only. Sophiagraph remains the durable second-brain memory
owner; third-brain providers expose cited repository, document, or artifact
graph state for inspection and context assembly. Local workbench actions are
provider-owned: unsupported providers return explicit unsupported statuses
instead of silently writing memory or source graph state.
