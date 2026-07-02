from __future__ import annotations

import ast
from pathlib import Path

from openminion_eval import (
    MemoryEffectivenessTrace,
    MemoryTraceClaim,
    MemoryTraceToolCall,
    build_memory_scorecard,
    compare_memory_scorecards,
    score_memory_case,
)
from openminion_eval.memory_effectiveness import (
    MemoryEffectivenessCase,
    MemoryExpectation,
)
from sophiagraph import (
    MemoryNamespace,
    MemoryRecord,
    SearchQueryOptions,
    SophiaGraphMemoryStore,
)


def _namespace() -> MemoryNamespace:
    return MemoryNamespace(agent_id="openminion", project_id="sophiagraph")


def _case() -> MemoryEffectivenessCase:
    return MemoryEffectivenessCase(
        case_id="repo-convention-test-command",
        family="repo_convention",
        prompt="Which validation command should run before release?",
        expectations=MemoryExpectation(
            required_saved_ids=("mem-release-check",),
            required_retrieved_ids=("mem-release-check",),
            required_used_ids=("mem-release-check",),
            required_claim_memory_ids=("mem-release-check",),
            required_tool_memory_ids=("mem-release-check",),
            expected_namespace="agent:openminion/project:sophiagraph",
            critical=True,
        ),
    )


def test_openminion_direct_library_scores_sophiagraph_memory_effectiveness() -> None:
    store = SophiaGraphMemoryStore()
    store.put_record(
        MemoryRecord(
            id="mem-release-check",
            scope="agent:openminion",
            type="procedure",
            key="release-check",
            title="Release validation command",
            content={"text": "Run make check before release."},
            namespace=_namespace(),
            created_at="2026-07-02T00:00:00+00:00",
            updated_at="2026-07-02T00:00:00+00:00",
            source="user_said",
        )
    )
    retrieved = store.search_records(
        SearchQueryOptions(
            query="make check",
            scopes=["agent:openminion"],
            namespaces=[_namespace()],
            limit=5,
        )
    )
    retrieved_ids = tuple(record.id for record in retrieved)
    case = _case()

    disabled = build_memory_scorecard(
        suite_id="openminion-sophiagraph-memory-effectiveness",
        run_id="disabled",
        case_results=[
            score_memory_case(
                case,
                MemoryEffectivenessTrace(
                    case_id=case.case_id,
                    run_id="disabled",
                    memory_mode="disabled",
                    namespace="agent:openminion/project:sophiagraph",
                ),
            )
        ],
    )
    enabled = build_memory_scorecard(
        suite_id="openminion-sophiagraph-memory-effectiveness",
        run_id="enabled",
        case_results=[
            score_memory_case(
                case,
                MemoryEffectivenessTrace(
                    case_id=case.case_id,
                    run_id="enabled",
                    memory_mode="enabled",
                    saved_memory_ids=("mem-release-check",),
                    retrieved_memory_ids=retrieved_ids,
                    used_memory_ids=("mem-release-check",),
                    supporting_claims=(
                        MemoryTraceClaim(
                            claim="This repo runs make check before release.",
                            memory_id="mem-release-check",
                        ),
                    ),
                    tool_calls=(
                        MemoryTraceToolCall(
                            tool="shell",
                            arguments_ref="sha256:make-check",
                            memory_ids=("mem-release-check",),
                        ),
                    ),
                    namespace="agent:openminion/project:sophiagraph",
                ),
            )
        ],
    )

    comparison = compare_memory_scorecards(disabled, enabled)[0]

    assert retrieved_ids == ("mem-release-check",)
    assert comparison.improved is True
    assert comparison.delta > 0


def test_memory_effectiveness_fixture_uses_public_imports_only() -> None:
    source = Path(__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }

    assert "sophiagraph.storage" not in modules
    assert not any(module.startswith("sophiagraph._") for module in modules)
    assert "openminion_eval.memory_effectiveness" in modules
