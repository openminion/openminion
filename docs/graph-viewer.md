# Visual Graph Viewer

Status: alpha
Last updated: 2026-07-24

OpenMinion can open current graph state in the shared GraphFakos visual viewer.
This surface is for inspection and navigation: it does not create memories,
replace Sophiagraph, or index repository content by itself.

## What You Can Inspect

1. `second` brain: durable OpenMinion memory stored through Sophiagraph-backed
   SQLite.
2. `third` brain: configured document, code, or artifact graph providers that
   expose a GraphFakos viewer envelope.

Use `status` first:

```bash
openminion graph status
```

It reports:

1. whether GraphFakos is installed,
2. where the second-brain memory database is,
3. which third-brain providers are configured and active,
4. which providers are visually ready,
5. exact next commands.

## Open Current Memory

```bash
openminion graph view --current
```

If you are not sure whether there is memory yet, run:

```bash
openminion graph status
openminion graph view --current --dry-run --json
```

If the graph is empty, create memory through the ordinary OpenMinion path, then
rerun `openminion graph view --current`. The viewer does not seed demo memory
or bypass the configured memory backend. `openminion graph status --json`
distinguishes a missing memory database from an existing database with no
visible records and includes the copyable command for creating memory through
OpenMinion.

`--current` is the user-facing shortcut for the current second-brain memory
graph. Use scope flags when you want a narrower view:

```bash
openminion graph view --current --agent openminion
openminion graph view --current --session my-session
openminion graph view --current --session my-session --agent openminion
```

For a no-browser check:

```bash
openminion graph view --current --dry-run --json
```

The JSON dry run is also the integration probe for wrappers. It includes graph
counts, active filters, facets, provider details, capabilities, empty-state
metadata, and a `viewer_manifest` summary with viewer-local actions such as
search, filtering, node inspection, neighborhood focus, path highlighting,
static export, provenance review, and citation copying when those details are
present.

For application wrappers, treat the dry-run payload as the stable readiness
probe:

```bash
openminion graph view --current --dry-run --json
```

Use the returned `viewer_manifest.viewer_state`,
`viewer_manifest.performance_budget`, `provider_details`, and
`provider_payload` fields to decide whether to show a launch button, refresh
button, filter controls, or empty-state guidance. A served second-brain viewer
can stream GraphFakos live `snapshot_reset` patches when Sophiagraph memory
changes. Static HTML and dry-run integrations should refresh by rerunning the
same graph request.

Served mode exposes GraphFakos' local `/api/live` event stream. OpenMinion keeps
that stream read-only: it refreshes the visual graph from the configured memory
backend and does not write, seed, or promote memory from the viewer.

The same command accepts viewer filters that map directly to GraphFakos'
toolbar controls:

```bash
openminion graph view --current --node-kind decision
openminion graph view --current --tag scope:agent:openminion
openminion graph view --current --source validated --min-score 0.8
openminion graph view --current --evidence-filter with_provenance
```

For a static page:

```bash
openminion graph view --current --html-out viewer.html
```

Second-brain nodes include memory type, tier, scope, confidence, namespace,
timestamps, provenance, citations, relation labels, and filter facets for node
kind, edge kind, tag, and source.

If the current memory graph is empty, the viewer reports an empty current-state
result. It does not seed demo data or write sample memories. The empty-state
payload includes next commands for checking status again, running a JSON
dry-run, and creating ordinary memory through an OpenMinion agent turn.

## Local Workbench Actions

When OpenMinion serves the local viewer, GraphFakos workbench forms are routed
through GraphFakos' provider-neutral action handler. This keeps the UI
consistent across OpenMinion, Sophiagraph, PragmaGraph, and future providers.

For the current Sophiagraph-backed second-brain viewer, durable writes remain
read-only from the visual surface. Graph edit actions and knowledge captures
return an explicit unsupported/provider-owned status unless a future provider
or OpenMinion review workflow implements that action. Users can still search,
filter, inspect, copy citations, export visible graph state, receive live
snapshot refreshes in the served viewer, and rerun the viewer request to refresh
static or wrapper views.

## Open A Third-Brain Provider

Third-brain providers open visually when their `knowledge_graphs.provider`
configuration includes `options.viewer_envelope_path`. PragmaGraph providers
may also use `options.snapshot_path`; that snapshot path is ready only when the
snapshot exists and the `openminion[viewer]` extra has installed PragmaGraph.

```bash
openminion graph view --brain third --provider repo_graph
```

If more than one third-brain provider is active, pass `--provider`. The error
message and `graph status` output both show the exact provider-specific
commands.

`graph status --json` includes diagnostic codes for common visual-readiness
states such as missing GraphFakos, no memory database yet, a missing
third-brain viewer envelope, a missing PragmaGraph runtime, or an unconfigured
provider envelope path. The human-readable status output also prints the
diagnostic code, ready/missing reason, sample memory count, and exact next
command.

## Try The Checked-In Example

From the package root:

```bash
python -m openminion \
  --home-root examples/graph-viewer \
  --config examples/graph-viewer/agents.json \
  graph status

python -m openminion \
  --home-root examples/graph-viewer \
  --config examples/graph-viewer/agents.json \
  graph view --brain third --provider repo_graph --html-out viewer.html
```

Open `examples/graph-viewer/README.md` for the full example.

## Embed In Another App

A wrapper does not need to depend on OpenMinion internals. Use these levels:

1. readiness/status: `openminion graph status --json`,
2. data probe: `openminion graph view --current --dry-run --json`,
3. static share/export: `openminion graph view --current --html-out viewer.html`,
4. interactive local lens: `openminion graph view --current --no-open`,
5. provider graph lens:
   `openminion graph view --brain third --provider <name> --no-open`.

The wrapper should present GraphFakos as a visual lens over current graph state.
It should not call it a memory backend, source indexer, or durable write owner.

## Boundary

GraphFakos is the viewer and provider-neutral graph lens. Sophiagraph remains
the durable second-brain owner. Graphify, PragmaGraph, and future document/code
providers remain third-brain sources unless they explicitly satisfy a durable
memory backend contract.

## Validation

The OpenMinion package keeps a focused graph-viewer regression suite covering
status, current-memory dry runs, served live-refresh patches, browser-observed
live state updates, static HTML, third-brain envelopes, provider conformance,
and real-browser smoke when the dev Playwright dependency and Chromium browser
are available:

```bash
PYTHONPATH=src:../graphfakos/src:../pragmagraph/src:../sophiagraph/src \
  python -m pytest -q -rs tests/context/knowledge/test_viewer.py
```

Use GraphFakos' browser suite for shared viewer behavior such as search,
filters, inspector panels, saved workspaces, keyboard navigation, and dense
graph rendering.

When validating a coordinated local package workspace, run the GraphFakos-owned
matrix from `../graphfakos`:

```bash
make integration-check
```
