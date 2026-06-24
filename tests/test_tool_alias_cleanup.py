from __future__ import annotations

from openminion.modules.tool.contracts import normalize_raw_model_tool_name


def test_only_canonical_model_tool_ids_resolve() -> None:
    assert normalize_raw_model_tool_name("file.read") == "file.read"
    assert normalize_raw_model_tool_name("functions.FILE.READ") == "file.read"
    assert normalize_raw_model_tool_name("web.search") == "web.search"


def test_runtime_and_legacy_aliases_are_removed() -> None:
    assert normalize_raw_model_tool_name("read_file") is None
    assert normalize_raw_model_tool_name("web_search") is None
    assert normalize_raw_model_tool_name("tavily.web.search") is None

    assert normalize_raw_model_tool_name("ddg_search") is None
    assert normalize_raw_model_tool_name("ddg-search") is None
    assert normalize_raw_model_tool_name("search") is None
    assert normalize_raw_model_tool_name("diskio_list") is None
    assert normalize_raw_model_tool_name("open_browser") is None
