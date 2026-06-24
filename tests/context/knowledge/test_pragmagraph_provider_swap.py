from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

from openminion.base.types import Message
from openminion.modules.context.knowledge import (
    CAPABILITY_CITATIONS,
    CAPABILITY_PROVENANCE,
    CAPABILITY_QUERY,
    GraphQueryRequest,
    KnowledgeGraphRegistry,
    LAYER_THIRD_BRAIN,
    build_knowledge_graph_service,
)
from openminion.modules.context.knowledge.constants import (
    EVENT_QUERY_COMPLETED,
    EVENT_QUERY_DEGRADED,
    EVENT_QUERY_STARTED,
    EVENT_SOURCE_RESOLVED,
)
from openminion.modules.context.knowledge.adapters.graphify import (
    GraphifyKnowledgeGraphSource,
)
from openminion.modules.context.knowledge.adapters.pragmagraph import (
    PragmaGraphKnowledgeGraphSource,
)
from openminion.services.constants import MEMORY_CAPSULE_STRATEGY_OFF
from openminion.services.gateway.context import build_turn_context
from tests.context.knowledge.conformance import (
    assert_query_results_are_interchangeable,
)
from tests.context.knowledge.fixtures import (
    TEST_QUERY,
    write_graphify_payload as _write_shared_graphify_payload,
    write_pragmagraph_snapshot as _write_shared_pragmagraph_snapshot,
)


def _write_graphify(path: Path) -> None:
    _write_shared_graphify_payload(path)


def _write_pragmagraph_snapshot(path: Path) -> None:
    _write_shared_pragmagraph_snapshot(path)


def _clear_pragmagraph_modules() -> None:
    for name in tuple(sys.modules):
        if name == "pragmagraph" or name.startswith("pragmagraph."):
            sys.modules.pop(name, None)


def _registry() -> KnowledgeGraphRegistry:
    registry = KnowledgeGraphRegistry()
    registry.register("graphify", GraphifyKnowledgeGraphSource)
    registry.register("pragmagraph", PragmaGraphKnowledgeGraphSource)
    return registry


class _SilentMemory:
    def build_context_with_metadata(
        self,
        *,
        session_id: str,
        user_message: str,
    ) -> tuple[str, dict[str, str]]:
        del session_id, user_message
        return "", {}


def _emit_capture(events: list[dict[str, Any]]):
    def _emit_memory_event(**kwargs: Any) -> None:
        events.append(dict(kwargs))

    return _emit_memory_event


def _build_context(service, events: list[dict[str, Any]]):
    return build_turn_context(
        history=[
            Message(
                channel="console",
                target="local-user",
                body=TEST_QUERY,
                metadata={"role": "user"},
            )
        ],
        agent_id="main",
        agent_memory=_SilentMemory(),
        logger=logging.getLogger("tests.context.knowledge.pragmagraph_swap"),
        emit_memory_event=_emit_capture(events),
        session_id="session-1",
        run_id="run-1",
        request_id="request-1",
        channel="console",
        target="local-user",
        user_message=TEST_QUERY,
        conversation_id="conversation-1",
        thread_id="thread-1",
        attach_id="attach-1",
        memory_capsule_strategy=MEMORY_CAPSULE_STRATEGY_OFF,
        memory_capsule_cache={},
        memory_dynamic_retrieval_enabled=False,
        knowledge_graphs=service,
    )


def test_graphify_only_and_pragmagraph_only_configs_swap_without_runtime_changes(
    tmp_path: Path,
) -> None:
    graphify_path = tmp_path / "graphify.json"
    pragma_path = tmp_path / "pragma.json"
    _write_graphify(graphify_path)
    _write_pragmagraph_snapshot(pragma_path)

    graphify_service = build_knowledge_graph_service(
        {
            "provider": {
                "active": ["repo_graph"],
                "providers": {
                    "repo_graph": {
                        "provider": "graphify",
                        "options": {"graph_path": str(graphify_path)},
                    }
                },
            }
        },
        registry=_registry(),
    )
    pragma_service = build_knowledge_graph_service(
        {
            "provider": {
                "active": ["repo_pragmas"],
                "providers": {
                    "repo_pragmas": {
                        "provider": "pragmagraph",
                        "options": {"snapshot_path": str(pragma_path)},
                    }
                },
            }
        },
        registry=_registry(),
    )

    graphify_result = graphify_service.query(GraphQueryRequest(query=TEST_QUERY))[0]
    pragma_result = pragma_service.query(GraphQueryRequest(query=TEST_QUERY))[0]

    assert graphify_result.provider == "repo_graph"
    assert pragma_result.provider == "repo_pragmas"
    assert graphify_result.items
    assert pragma_result.items
    assert_query_results_are_interchangeable(graphify_result, pragma_result)


def test_graphify_and_pragmagraph_can_be_coenabled(tmp_path: Path) -> None:
    graphify_path = tmp_path / "graphify.json"
    pragma_path = tmp_path / "pragma.json"
    _write_graphify(graphify_path)
    _write_pragmagraph_snapshot(pragma_path)

    service = build_knowledge_graph_service(
        {
            "provider": {
                "active": ["repo_graph", "repo_pragmas"],
                "providers": {
                    "repo_graph": {
                        "provider": "graphify",
                        "required_capabilities": [
                            CAPABILITY_QUERY,
                            CAPABILITY_CITATIONS,
                            CAPABILITY_PROVENANCE,
                        ],
                        "options": {"graph_path": str(graphify_path)},
                    },
                    "repo_pragmas": {
                        "provider": "pragmagraph",
                        "required_capabilities": [
                            CAPABILITY_QUERY,
                            CAPABILITY_CITATIONS,
                            CAPABILITY_PROVENANCE,
                        ],
                        "options": {"snapshot_path": str(pragma_path)},
                    },
                },
            }
        },
        registry=_registry(),
    )

    results = service.query(GraphQueryRequest(query=TEST_QUERY))

    assert [result.provider for result in results] == ["repo_graph", "repo_pragmas"]
    assert all(result.items for result in results)
    assert [
        source.name for source in service.list_sources(layer=LAYER_THIRD_BRAIN)
    ] == [
        "repo_graph",
        "repo_pragmas",
    ]
    assert_query_results_are_interchangeable(results[0], results[1])


def test_disabled_pragmagraph_provider_does_not_import_package(monkeypatch) -> None:
    _clear_pragmagraph_modules()

    service = build_knowledge_graph_service(
        {
            "provider": {
                "active": [],
                "providers": {
                    "repo_pragmas": {
                        "provider": "pragmagraph",
                        "enabled": False,
                    }
                },
            }
        },
        registry=_registry(),
    )

    assert service.list_sources(layer=LAYER_THIRD_BRAIN) == ()
    assert "pragmagraph" not in sys.modules


def test_gateway_context_uses_graphify_and_pragmagraph_with_attribution(
    tmp_path: Path,
) -> None:
    graphify_path = tmp_path / "graphify.json"
    pragma_path = tmp_path / "pragma.json"
    _write_graphify(graphify_path)
    _write_pragmagraph_snapshot(pragma_path)
    events: list[dict[str, Any]] = []
    service = build_knowledge_graph_service(
        {
            "provider": {
                "active": ["repo_graph", "repo_pragmas"],
                "providers": {
                    "repo_graph": {
                        "provider": "graphify",
                        "options": {"graph_path": str(graphify_path)},
                    },
                    "repo_pragmas": {
                        "provider": "pragmagraph",
                        "options": {"snapshot_path": str(pragma_path)},
                    },
                },
            }
        },
        registry=_registry(),
    )

    context = _build_context(service, events)

    assert "Provider: repo_graph" in context.history[-1].body
    assert "Provider: repo_pragmas" in context.history[-1].body
    assert context.history[-1].metadata["graph_scope"] == "provider"
    assert context.knowledge_graph_meta["knowledge_graph_providers"] == (
        "repo_graph,repo_pragmas"
    )
    assert [event["event_type"] for event in events] == [
        EVENT_SOURCE_RESOLVED,
        EVENT_QUERY_STARTED,
        EVENT_QUERY_COMPLETED,
    ]


def test_gateway_context_degrades_when_pragmagraph_fails_but_graphify_succeeds(
    tmp_path: Path,
) -> None:
    graphify_path = tmp_path / "graphify.json"
    _write_graphify(graphify_path)
    events: list[dict[str, Any]] = []
    service = build_knowledge_graph_service(
        {
            "provider": {
                "active": ["repo_graph", "repo_pragmas"],
                "providers": {
                    "repo_graph": {
                        "provider": "graphify",
                        "options": {"graph_path": str(graphify_path)},
                    },
                    "repo_pragmas": {
                        "provider": "pragmagraph",
                        "options": {"snapshot_path": str(tmp_path / "missing.json")},
                    },
                },
            }
        },
        registry=_registry(),
    )

    context = _build_context(service, events)

    assert "Provider: repo_graph" in context.history[-1].body
    assert (
        "repo_pragmas"
        in context.knowledge_graph_meta["knowledge_graph_failed_providers"]
    )
    assert [event["event_type"] for event in events] == [
        EVENT_SOURCE_RESOLVED,
        EVENT_QUERY_STARTED,
        EVENT_QUERY_DEGRADED,
        EVENT_QUERY_COMPLETED,
    ]


def test_gateway_context_degrades_when_graphify_fails_but_pragmagraph_succeeds(
    tmp_path: Path,
) -> None:
    pragma_path = tmp_path / "pragma.json"
    _write_pragmagraph_snapshot(pragma_path)
    events: list[dict[str, Any]] = []
    service = build_knowledge_graph_service(
        {
            "provider": {
                "active": ["repo_graph", "repo_pragmas"],
                "providers": {
                    "repo_graph": {
                        "provider": "graphify",
                        "options": {"capabilities": []},
                    },
                    "repo_pragmas": {
                        "provider": "pragmagraph",
                        "options": {"snapshot_path": str(pragma_path)},
                    },
                },
            }
        },
        registry=_registry(),
    )

    context = _build_context(service, events)

    assert "Provider: repo_pragmas" in context.history[-1].body
    assert context.knowledge_graph_meta["knowledge_graph_failed_providers"] == (
        "repo_graph"
    )
    assert [event["event_type"] for event in events] == [
        EVENT_SOURCE_RESOLVED,
        EVENT_QUERY_STARTED,
        EVENT_QUERY_DEGRADED,
        EVENT_QUERY_COMPLETED,
    ]
