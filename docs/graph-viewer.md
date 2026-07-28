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

The same command accepts viewer filters that map directly to GraphFakos'
toolbar controls:

```bash
openminion graph view --current --node-kind decision
openminion graph view --current --tag scope:agent:openminion
openminion graph view --current --source operator --min-score 0.8
openminion graph view --current --evidence-filter with_provenance
```

For a static page:

```bash
openminion graph view --current --html-out viewer.html
```

Second-brain nodes include memory type, tier, scope, confidence, namespace,
timestamps, provenance, citations, relation labels, and filter facets for node
kind, edge kind, tag, and source.

## Open A Third-Brain Provider

Third-brain providers open visually when their `knowledge_graphs.provider`
configuration includes `options.viewer_envelope_path`. PragmaGraph providers
may also use `options.snapshot_path`.

```bash
openminion graph view --brain third --provider repo_graph
```

If more than one third-brain provider is active, pass `--provider`. The error
message and `graph status` output both show the exact provider-specific
commands.

`graph status --json` includes diagnostic codes for common visual-readiness
states such as missing GraphFakos, no memory database yet, a missing
third-brain viewer envelope, or an unconfigured provider envelope path. The
human-readable status output also prints the diagnostic code, ready/missing
reason, sample memory count, and exact next command.

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

## Boundary

GraphFakos is the viewer and provider-neutral graph lens. Sophiagraph remains
the durable second-brain owner. Graphify, PragmaGraph, and future document/code
providers remain third-brain sources unless they explicitly satisfy a durable
memory backend contract.
