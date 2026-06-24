# `modules/context/knowledge`

Owner: `openminion-knowledge-graphs`
Shape: `template-aligned`
Runtime peer: gateway context assembly and runtime bootstrap
Status: executor-complete; parent and abstraction follow-ons are closed

## Charter

This package is the **OpenMinion-side adapter and registry boundary** for graph
context providers. It does not implement the Sophiagraph or PragmaGraph graph
engines, and it must not absorb package-side graph logic from either sibling
package.

Providers register into one of two layers:

1. `second_brain` — durable agent memory graph (e.g. Sophiagraph). Single active provider per OpenMinion instance.
2. `provider` — external document, code, artifact, or hosted graph context (Graphify and PragmaGraph today). Zero or more active providers.

The package defines the provider-neutral OpenMinion contract that both layers
share: typed DTOs, capability vocabulary, typed errors, and the
`KnowledgeGraphSource` protocol. Registry, adapters, and runtime wiring live in
sibling files / packages added by TBKG-02 (`registry.py`) and TBKG-04
(`adapters/graphify.py`).

## Why this lives under `context/`

`modules/context/knowledge` exists because graph-provider selection is a
context-input seam, not a standalone OpenMinion module. OpenMinion still needs
a stable internal contract for provider registration, gateway context assembly,
graceful degradation, telemetry, and config compatibility. The reusable graph
packages remain external:

- `sophiagraph` owns durable agent memory
  graph substrate behavior.
- `pragmagraph` owns static observed-fact
  graph indexing/query behavior.

OpenMinion may import PragmaGraph only behind `adapters/pragmagraph.py`;
PragmaGraph and Sophiagraph must not import OpenMinion. The config namespace
remains `knowledge_graphs:` because operators select graph providers through a
single typed context-source mapping even though the package now lives under the
context owner.

## Naming boundary

The native third-brain package name is **PragmaGraph**. Graphify is the first
read-oriented provider adapter behind the `provider` layer; it is not the
layer name. Sophiagraph remains the second-brain durable memory graph.

Future-agent quick reference:
second-vs-third-brain quick reference.
OpenMinion provider-abstraction readiness tracker:
provider-abstraction readiness tracker.

Rule of thumb: PragmaGraph/third-brain providers index static, observed,
reproducible facts from code, docs, artifacts, and history. Sophiagraph stores
agent-owned memory: learned preferences, operator pins, decisions, summaries,
and judgments. Sophia may cite `pragma://...` evidence; Pragma never stores
Sophia's judgments.

Do not collapse package and platform work: PragmaGraph owns package-side graph
contracts, snapshots, indexers, query APIs, and handoff fixtures. Sophiagraph
owns package-side durable memory substrate behavior. OpenMinion owns provider
registration, conformance tests, optional dependency isolation, context
assembly, telemetry, graceful fallback, and provider swapability.
The OpenMinion-side PragmaGraph adapter/swapability bridge is tracked by
the PragmaGraph provider adapter swapability tracker.

## Layers vs tags

Layer membership is the role. Inside a layer, descriptive labels are *tags* (`document_graph`, `code_graph`, `artifact_graph`, `hosted_graph`, `hybrid_graph`) — never peer roles. Routing is capability-first, not tag-first.

## Capability contract

Runtime routes by advertised capability rather than provider name. Capabilities include:

- `query`, `path`, `neighborhood`, `explain`, `refresh`, `watch`
- `citations`, `provenance`
- `writable_graph`
- `durable_memory` — durable-memory backend contract (second-brain only)
- `promote_candidates` — candidate staging / approval / promotion lifecycle (second-brain only)
- `promotes_to_durable` — hybrid providers that route durable writes through a second-brain delegate

Hybrid providers must not advertise `durable_memory` directly; they advertise `promotes_to_durable` and write through a configured second-brain delegate (typically Sophiagraph).

## Runtime behavior

OpenMinion builds a `KnowledgeGraphService` during runtime bootstrap and passes
it into the gateway. Context assembly queries active `provider` providers
for cited static graph facts and appends them as a separate system context
block with `graph_scope=provider`. This block is additive to second-brain
memory context; it does not write memories and does not infer preferences,
decisions, or summaries.

Provider failures are typed and degrade locally. If every active third-brain
provider fails, the turn continues without graph context and emits
`knowledge_graph.query.failed`. If one provider fails while another returns
context, the turn keeps the successful cited facts and emits
`knowledge_graph.query.degraded` followed by
`knowledge_graph.query.completed`.

## Interchangeability contract

For third-brain providers, "interchangeable" means:

- the same provider-neutral DTOs (`GraphQueryResult`, `GraphContextItem`,
  `GraphPathResult`, health envelopes, omitted items),
- the same runtime degradation behavior,
- the same gateway context assembly contract,
- and support for shared fixture-driven contract tests.

It does not require byte-identical scoring, identical internal ranking logic,
or identical backend storage formats. Providers may rank or refresh
differently, but they must remain compatible at the OpenMinion contract layer.

## Boundaries

- `runtime.memory_provider` and `memory.backend.provider` remain the authoritative second-brain durable-memory selectors. The `knowledge_graphs` config namespace is parallel and does not modify those paths.
- The package does not import from `services/` or `api/` (CI-enforced import boundary).
- Provider SDKs and command runners stay behind adapter boundaries.
- Provider-swap config examples live at
  the package docs examples bundle.

## Canonical files

| File | Purpose |
| --- | --- |
| `interfaces.py` | `KnowledgeGraphSource` Protocol + capability-to-method map |
| `models.py` | Provider-neutral DTOs (`GraphQueryResult`, `GraphContextItem`, capability/health) |
| `config.py` | Operator-tunable configs (`KnowledgeGraphsConfig`, layer + provider + retrieval/refresh) |
| `constants.py` | Fixed vocabulary (layer names, provider tags, capability names, telemetry events) |
| `contracts.py` | `KNOWLEDGE_GRAPH_CONTRACT_VERSION` literal |
| `errors.py` | Typed error hierarchy rooted at `KnowledgeGraphError` |
| `registry.py` | Provider factory registry and capability validation |
| `service.py` | Active provider service and capability-gated dispatch |
| `adapters/graphify.py` | Read-oriented Graphify third-brain adapter |
| `adapters/pragmagraph.py` | Read-oriented PragmaGraph third-brain adapter |

## Result-envelope alignment

The `GraphQueryResult` envelope is the shared result shape for second-brain
Sophiagraph retrieval and third-brain Graphify retrieval. The Sophiagraph
context-assembly follow-on coordinates its public context package against this
DTO.
