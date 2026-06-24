from __future__ import annotations

from openminion.cli.parser.contracts import CLI_INTERFACE_VERSION


class DemoThirdBrainProvider:
    contract_version: str = CLI_INTERFACE_VERSION

    def __init__(self) -> None:
        self._refresh_count = 0
        self._query_payloads = [
            {
                "provider": "pragmagraph",
                "layer": "provider",
                "tags": ["code_graph", "document_graph"],
                "items": [
                    {
                        "provider": "pragmagraph",
                        "source_graph_id": "pragma-demo",
                        "node_or_edge_id": "symbol:KnowledgeGraphService.query",
                        "source_ref": {
                            "path": "openminion/src/openminion/modules/context/knowledge/service.py",
                            "line": 101,
                            "page": None,
                            "span": None,
                        },
                        "snippet": "def query(self, request: GraphQueryRequest, *, provider_names: Iterable[str] | None = None, layer: str | None = None) -> tuple[GraphQueryResult, ...]:",
                        "score": 0.97,
                        "metadata": {"kind": "symbol", "language": "python"},
                    },
                    {
                        "provider": "pragmagraph",
                        "source_graph_id": "pragma-demo",
                        "node_or_edge_id": "file:references/third-brain-workbench-spec.md",
                        "source_ref": {
                            "path": "references/third-brain-workbench-spec.md",
                            "line": 1,
                            "page": None,
                            "span": None,
                        },
                        "snippet": "Define the first operator-facing UI/workbench for OpenMinion's third-brain layer.",
                        "score": 0.91,
                        "metadata": {"kind": "document", "language": "markdown"},
                    },
                ],
                "paths": [],
                "omitted": [
                    {
                        "provider": "pragmagraph",
                        "node_or_edge_id": "symbol:KnowledgeGraphService.refresh",
                        "reason": "budget",
                        "details": {"max_results": 2},
                    }
                ],
                "diagnostics": {
                    "graph_id": "pragma-demo",
                    "schema_version": "v0",
                    "match_mode": "lexical",
                },
            },
            {
                "provider": "graphify",
                "layer": "provider",
                "tags": ["document_graph"],
                "items": [
                    {
                        "provider": "graphify",
                        "source_graph_id": "graphify-demo",
                        "node_or_edge_id": "doc:boundary",
                        "source_ref": {
                            "path": "references/brain-boundary-guide.md",
                            "line": 1,
                            "page": None,
                            "span": None,
                        },
                        "snippet": "Rule of thumb: PragmaGraph indexes observed facts; Sophiagraph stores learned memory.",
                        "score": 0.88,
                        "metadata": {"kind": "document", "language": "markdown"},
                    }
                ],
                "paths": [],
                "omitted": [],
                "diagnostics": {
                    "graph_id": "graphify-demo",
                    "schema_version": "v1",
                    "match_mode": "lexical",
                },
            },
        ]

    def list_provider_status(self) -> list[dict]:
        return [
            {
                "provider": "pragmagraph",
                "layer": "provider",
                "ok": True,
                "detail": "ready",
                "tags": ["code_graph", "document_graph"],
                "capabilities": [
                    "query",
                    "neighborhood",
                    "path",
                    "refresh",
                    "provenance",
                    "citations",
                ],
                "diagnostics": {
                    "graph_id": "pragma-demo",
                    "last_refresh": f"demo-{self._refresh_count}",
                },
            },
            {
                "provider": "graphify",
                "layer": "provider",
                "ok": True,
                "detail": "ready",
                "tags": ["document_graph"],
                "capabilities": ["query", "neighborhood", "provenance", "citations"],
                "diagnostics": {
                    "graph_id": "graphify-demo",
                    "last_refresh": f"demo-{self._refresh_count}",
                },
            },
        ]

    def search(
        self,
        query: str,
        *,
        provider_names: list[str] | None = None,
        max_results: int = 20,
    ) -> list[dict]:
        del max_results
        normalized = str(query or "").strip().lower()
        providers = set(provider_names or [])
        payloads = []
        for payload in self._query_payloads:
            if providers and payload["provider"] not in providers:
                continue
            if not normalized:
                continue
            hits = [
                item
                for item in payload["items"]
                if normalized in str(item["node_or_edge_id"]).lower()
                or normalized in str(item["snippet"]).lower()
                or normalized in str(item["source_ref"]["path"]).lower()
            ]
            if not hits:
                continue
            payloads.append({**payload, "items": hits})
        return payloads

    def neighborhood(
        self,
        entity_id: str,
        *,
        provider_names: list[str] | None = None,
        depth: int = 1,
        max_results: int = 20,
    ) -> list[dict]:
        del depth, max_results
        providers = set(provider_names or [])
        if providers and "pragmagraph" not in providers:
            return []
        return [
            {
                "provider": "pragmagraph",
                "layer": "provider",
                "tags": ["code_graph"],
                "items": [
                    {
                        "provider": "pragmagraph",
                        "source_graph_id": "pragma-demo",
                        "node_or_edge_id": entity_id,
                        "source_ref": {
                            "path": "openminion/src/openminion/modules/context/knowledge/service.py",
                            "line": 101,
                            "page": None,
                            "span": None,
                        },
                        "snippet": "Selected node",
                        "score": 1.0,
                        "metadata": {"kind": "symbol"},
                    },
                    {
                        "provider": "pragmagraph",
                        "source_graph_id": "pragma-demo",
                        "node_or_edge_id": "symbol:KnowledgeGraphService.refresh",
                        "source_ref": {
                            "path": "openminion/src/openminion/modules/context/knowledge/service.py",
                            "line": 152,
                            "page": None,
                            "span": None,
                        },
                        "snippet": "refresh emits typed start/fail/completed events",
                        "score": 0.82,
                        "metadata": {"kind": "symbol"},
                    },
                ],
                "paths": [],
                "omitted": [],
                "diagnostics": {"graph_id": "pragma-demo", "mode": "neighborhood"},
            }
        ]

    def path(
        self,
        source_entity_id: str,
        target_entity_id: str,
        *,
        provider_names: list[str] | None = None,
        max_hops: int = 4,
    ) -> list[dict]:
        del max_hops
        providers = set(provider_names or [])
        if providers and "pragmagraph" not in providers:
            return []
        return [
            {
                "provider": "pragmagraph",
                "layer": "provider",
                "paths": [
                    {
                        "provider": "pragmagraph",
                        "nodes": [
                            {
                                "provider": "pragmagraph",
                                "source_graph_id": "pragma-demo",
                                "node_or_edge_id": source_entity_id,
                                "source_ref": {
                                    "path": "openminion/src/openminion/modules/context/knowledge/service.py",
                                    "line": 101,
                                    "page": None,
                                    "span": None,
                                },
                                "snippet": "source node",
                                "score": 1.0,
                                "metadata": {"kind": "symbol"},
                            },
                            {
                                "provider": "pragmagraph",
                                "source_graph_id": "pragma-demo",
                                "node_or_edge_id": target_entity_id,
                                "source_ref": {
                                    "path": "openminion/src/openminion/modules/context/knowledge/service.py",
                                    "line": 152,
                                    "page": None,
                                    "span": None,
                                },
                                "snippet": "target node",
                                "score": 0.92,
                                "metadata": {"kind": "symbol"},
                            },
                        ],
                        "edges": [
                            {
                                "type": "calls",
                                "source": source_entity_id,
                                "target": target_entity_id,
                            }
                        ],
                        "explanation": "static path between source and target",
                        "score": 0.92,
                    }
                ],
                "omitted": [],
                "diagnostics": {"graph_id": "pragma-demo", "mode": "path"},
            }
        ]

    def refresh(
        self,
        *,
        provider_names: list[str] | None = None,
        full: bool = False,
    ) -> list[dict]:
        del full
        self._refresh_count += 1
        providers = set(provider_names or []) or {"pragmagraph", "graphify"}
        return [
            {
                "provider": provider,
                "layer": "provider",
                "ok": True,
                "refreshed_at": f"demo-{self._refresh_count}",
                "counts": {"items": 3},
                "diagnostics": {"mode": "manual"},
            }
            for provider in sorted(providers)
        ]


__all__ = ["DemoThirdBrainProvider"]
