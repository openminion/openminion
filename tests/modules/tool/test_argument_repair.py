from openminion.modules.tool.runtime.argument_repair import (
    missing_simple_required_fields,
    synthesize_simple_tool_arguments,
)


def test_web_search_missing_required_fields_accepts_q_alias() -> None:
    missing = missing_simple_required_fields(
        tool_name="web.search",
        arguments={"q": "latest Iran news 2025"},
    )
    assert missing == ()


def test_web_search_synthesizes_query_from_q_alias_and_count_alias() -> None:
    repaired = synthesize_simple_tool_arguments(
        tool_name="web.search",
        user_input="ignored because q already exists",
        existing_args={"q": "latest Iran news 2025", "count": 10},
    )
    assert repaired == {
        "q": "latest Iran news 2025",
        "count": 10,
        "query": "latest Iran news 2025",
        "max_results": 10,
    }
