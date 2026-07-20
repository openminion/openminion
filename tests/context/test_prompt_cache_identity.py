from openminion.modules.context.pack.finalize import build_prompt_cache_key
from openminion.modules.context.prefix import PinnedPrefixBuilder, PrefixCacheAdapter
from openminion.modules.context.schemas import (
    BuildConstraints,
    BuildPackRequest,
    ContextSegment,
)


def _request() -> BuildPackRequest:
    return BuildPackRequest(
        session_id="session-1",
        agent_id="agent-1",
        purpose="decide",
        query="hi",
        model_hint="model-1",
    )


def _static_segment() -> ContextSegment:
    return ContextSegment(
        id="identity",
        bucket="static_prefix",
        content="stable identity",
        token_estimate=2,
    )


def test_prompt_cache_key_invalidates_when_execution_tool_schema_changes() -> None:
    common = {
        "prefix_builder": PinnedPrefixBuilder(),
        "prefix_cache_adapter": PrefixCacheAdapter("generic"),
        "request": _request(),
        "segments": [_static_segment()],
        "prompt_tool_schemas": [],
    }

    first = build_prompt_cache_key(
        constraints=BuildConstraints(
            runtime_tool_schemas=[{"name": "file.read", "input_schema": {}}]
        ),
        **common,
    )
    second = build_prompt_cache_key(
        constraints=BuildConstraints(
            runtime_tool_schemas=[{"name": "file.write", "input_schema": {}}]
        ),
        **common,
    )

    assert first[0] == second[0]
    assert first[1] != second[1]


def test_prompt_cache_key_is_stable_for_reordered_execution_tools() -> None:
    common = {
        "prefix_builder": PinnedPrefixBuilder(),
        "prefix_cache_adapter": PrefixCacheAdapter("generic"),
        "request": _request(),
        "segments": [_static_segment()],
        "prompt_tool_schemas": [],
    }
    tools = [
        {"name": "file.read", "input_schema": {}},
        {"name": "file.write", "input_schema": {}},
    ]

    first = build_prompt_cache_key(
        constraints=BuildConstraints(runtime_tool_schemas=tools),
        **common,
    )
    second = build_prompt_cache_key(
        constraints=BuildConstraints(runtime_tool_schemas=list(reversed(tools))),
        **common,
    )

    assert first == second
