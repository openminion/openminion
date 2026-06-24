from __future__ import annotations

from pathlib import Path


def _tools_root() -> Path:
    return Path(__file__).resolve().parents[2] / "src" / "openminion" / "tools"


def test_tools_root_files_match_category_layout_contract() -> None:
    root = _tools_root()
    root_files = {path.name for path in root.iterdir() if path.is_file()}
    assert root_files == {
        "README.md",
        "__init__.py",
        "__main__.py",
        "config.py",
        "constants.py",
        "decorator.py",
        "env.py",
    }


def test_multi_provider_categories_use_providers_subpackages() -> None:
    root = _tools_root()
    expected = {
        "search": {"brave", "firecrawl", "serpapi", "serper", "tavily"},
        "browser": {"pinchtab", "playwright"},
        "weather": {"openmeteo", "weatherapi"},
        "fetch": {"scrapling"},
    }
    for category, providers in expected.items():
        providers_root = root / category / "providers"
        assert (providers_root / "__init__.py").exists()
        discovered = {path.name for path in providers_root.iterdir() if path.is_dir()}
        assert providers.issubset(discovered)

    assert (root / "fetch" / "providers" / "core_http.py").exists()


def test_flat_variant_packages_are_retired_from_tools_root() -> None:
    root = _tools_root()
    for retired in (
        "browser_pinchtab",
        "browser_playwright",
        "fetch_scrapling",
        "search_brave",
        "search_firecrawl",
        "search_serpapi",
        "search_serper",
        "search_tavily",
        "weather_openmeteo",
        "weather_weatherapi",
    ):
        assert not (root / retired).exists()


def test_bootstrap_entries_use_nested_provider_paths() -> None:
    from openminion.modules.tool.bootstrap import _TOOL_BOOTSTRAP_ENTRIES

    nested_entries = {
        entry.module_name
        for entry in _TOOL_BOOTSTRAP_ENTRIES
        if ".providers." in entry.module_name
    }
    assert {
        "openminion.tools.browser.providers.pinchtab",
        "openminion.tools.browser.providers.playwright",
        "openminion.tools.fetch.providers.scrapling",
        "openminion.tools.search.providers.brave",
        "openminion.tools.search.providers.firecrawl",
        "openminion.tools.search.providers.serpapi",
        "openminion.tools.search.providers.serper",
        "openminion.tools.search.providers.tavily",
        "openminion.tools.weather.providers.openmeteo",
        "openminion.tools.weather.providers.weatherapi",
    }.issubset(nested_entries)
