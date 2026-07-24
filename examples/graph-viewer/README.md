# Graph Viewer Example

This example opens OpenMinion's visual graph lens over a small third-brain
provider envelope. It is intentionally static: the envelope is a checked-in
sample of the shape a document, code, or artifact graph provider can expose to
GraphFakos.

From the package root:

```bash
python -m openminion \
  --home-root examples/graph-viewer \
  --config examples/graph-viewer/agents.json \
  graph status

python -m openminion \
  --home-root examples/graph-viewer \
  --config examples/graph-viewer/agents.json \
  graph view --brain third --provider repo_graph
```

For a no-browser proof:

```bash
python -m openminion \
  --home-root examples/graph-viewer \
  --config examples/graph-viewer/agents.json \
  graph view --brain third --provider repo_graph --html-out viewer.html
```

The viewer is a lens only. Sophiagraph remains the durable second-brain memory
owner; third-brain providers expose cited repository, document, or artifact
graph state for inspection and context assembly.
