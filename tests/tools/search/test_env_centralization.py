from __future__ import annotations

from pathlib import Path

from openminion.base.config.env import resolve_environment_config_with_explicit_env
from openminion.base.config.env.registry import get_env_var_specs
from openminion.tools import env as tools_env


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _read(rel_path: str) -> str:
    return (_repo_root() / rel_path).read_text(encoding="utf-8")


def test_search_family_avoids_direct_process_env_access() -> None:
    paths = (
        "src/openminion/tools/search/plugin.py",
        "src/openminion/tools/search/providers/tavily/plugin.py",
        "src/openminion/tools/search/providers/tavily/search.py",
        "src/openminion/tools/search/providers/brave/provider.py",
        "src/openminion/tools/search/providers/brave/plugin.py",
        "src/openminion/tools/search/providers/serper/provider.py",
        "src/openminion/tools/search/providers/serper/plugin.py",
        "src/openminion/tools/search/providers/tinyfish/provider.py",
        "src/openminion/tools/search/providers/tinyfish/plugin.py",
    )
    for rel_path in paths:
        text = _read(rel_path)
        assert "os.getenv(" not in text, rel_path
        assert "os.environ" not in text, rel_path


def test_fetch_path_avoids_direct_process_env_access() -> None:
    text = _read("src/openminion/tools/fetch/plugin.py")
    assert "os.getenv(" not in text
    assert "os.environ" not in text
    provider_text = _read("src/openminion/tools/fetch/providers/tinyfish/provider.py")
    assert "os.getenv(" not in provider_text
    assert "os.environ" not in provider_text


def test_search_family_uses_shared_tools_env_module() -> None:
    paths = (
        "src/openminion/tools/search/plugin.py",
        "src/openminion/tools/search/providers/tavily/plugin.py",
        "src/openminion/tools/search/providers/tavily/search.py",
        "src/openminion/tools/search/providers/brave/provider.py",
        "src/openminion/tools/search/providers/brave/plugin.py",
        "src/openminion/tools/search/providers/serper/provider.py",
        "src/openminion/tools/search/providers/serper/plugin.py",
        "src/openminion/tools/search/providers/tinyfish/provider.py",
        "src/openminion/tools/search/providers/tinyfish/plugin.py",
    )
    for rel_path in paths:
        text = _read(rel_path)
        assert "openminion.tools.search.env" not in text, rel_path
    assert not (_repo_root() / "src/openminion/tools/search/env.py").exists()


def test_serpapi_env_accessors_are_centralized() -> None:
    env = resolve_environment_config_with_explicit_env(
        env={
            "SERPAPI_API_KEY": "serp-key",
            "SERPAPI_API_URL": "https://serpapi.example.test/search",
            "SERPAPI_TIMEOUT_SECONDS": "17.5",
            "OPENMINION_WEB_SEARCH_PROVIDER": "SERPAPI",
        }
    )

    assert env.serpapi_api_key == "serp-key"
    assert tools_env.get_serpapi_api_key(env=env) == "serp-key"
    assert (
        tools_env.get_serpapi_api_url(env=env) == "https://serpapi.example.test/search"
    )
    assert tools_env.get_serpapi_timeout_seconds(env=env) == 17.5
    assert tools_env.get_web_search_provider_override(env=env) == "serpapi"


def test_serpapi_env_registry_and_provider_enum_are_registered() -> None:
    specs = {item.name: item for item in get_env_var_specs()}

    assert specs["OPENMINION_WEB_SEARCH_PROVIDER"].value_type == (
        "enum(auto|brave|tavily|serpapi|firecrawl|serper|tinyfish)"
    )
    assert specs["OPENMINION_WEB_SEARCH_PROVIDER"].owner == "tools/search"
    assert specs["SERPAPI_API_KEY"].owner == "tools/search/providers/serpapi"
    assert specs["SERPAPI_API_URL"].default == "https://serpapi.com/search"
    assert specs["SERPAPI_TIMEOUT_SECONDS"].default == "20.0"


def test_serper_env_accessors_are_centralized() -> None:
    env = resolve_environment_config_with_explicit_env(
        env={
            "SERPER_API_KEY": "serper-key",
            "SERPER_API_URL": "https://google.serper.example.test/search",
            "SERPER_TIMEOUT_SECONDS": "18.5",
            "OPENMINION_WEB_SEARCH_PROVIDER": "SERPER",
        }
    )

    assert env.serper_api_key == "serper-key"
    assert tools_env.get_serper_api_key(env=env) == "serper-key"
    assert (
        tools_env.get_serper_api_url(env=env)
        == "https://google.serper.example.test/search"
    )
    assert tools_env.get_serper_timeout_seconds(env=env) == 18.5
    assert tools_env.get_web_search_provider_override(env=env) == "serper"


def test_serper_env_registry_and_provider_enum_are_registered() -> None:
    specs = {item.name: item for item in get_env_var_specs()}

    assert specs["OPENMINION_WEB_SEARCH_PROVIDER"].value_type == (
        "enum(auto|brave|tavily|serpapi|firecrawl|serper|tinyfish)"
    )
    assert specs["SERPER_API_KEY"].owner == "tools/search/providers/serper"
    assert specs["SERPER_API_URL"].default == "https://google.serper.dev/search"
    assert specs["SERPER_TIMEOUT_SECONDS"].default == "20.0"


def test_firecrawl_env_accessors_are_centralized() -> None:
    env = resolve_environment_config_with_explicit_env(
        env={
            "FIRECRAWL_API_KEY": "firecrawl-key",
            "FIRECRAWL_API_URL": "https://firecrawl.example.test",
            "FIRECRAWL_TIMEOUT_SECONDS": "12.5",
            "OPENMINION_WEB_SEARCH_PROVIDER": "FIRECRAWL",
        }
    )

    assert env.firecrawl_api_key == "firecrawl-key"
    assert tools_env.get_firecrawl_api_key(env=env) == "firecrawl-key"
    assert tools_env.get_firecrawl_api_url(env=env) == "https://firecrawl.example.test"
    assert tools_env.get_firecrawl_timeout_seconds(env=env) == 12.5
    assert tools_env.get_web_search_provider_override(env=env) == "firecrawl"


def test_firecrawl_env_registry_is_registered_once_at_shared_owner() -> None:
    specs = {item.name: item for item in get_env_var_specs()}

    assert specs["FIRECRAWL_API_KEY"].owner == "tools/firecrawl providers"
    assert specs["FIRECRAWL_API_URL"].default == "https://api.firecrawl.dev"
    assert specs["FIRECRAWL_TIMEOUT_SECONDS"].default == "20.0"


def test_tinyfish_env_accessors_are_centralized() -> None:
    env = resolve_environment_config_with_explicit_env(
        env={
            "TINYFISH_API_KEY": "tinyfish-key",
            "TINYFISH_SEARCH_API_URL": "https://api.search.tinyfish.example.test",
            "TINYFISH_SEARCH_TIMEOUT_SECONDS": "18.0",
            "TINYFISH_FETCH_API_URL": "https://api.fetch.tinyfish.example.test",
            "TINYFISH_FETCH_TIMEOUT_SECONDS": "145.0",
            "OPENMINION_WEB_SEARCH_PROVIDER": "TINYFISH",
        }
    )

    assert env.tinyfish_api_key == "tinyfish-key"
    assert tools_env.get_tinyfish_api_key(env=env) == "tinyfish-key"
    assert (
        tools_env.get_tinyfish_search_api_url(env=env)
        == "https://api.search.tinyfish.example.test"
    )
    assert tools_env.get_tinyfish_search_timeout_seconds(env=env) == 18.0
    assert (
        tools_env.get_tinyfish_fetch_api_url(env=env)
        == "https://api.fetch.tinyfish.example.test"
    )
    assert tools_env.get_tinyfish_fetch_timeout_seconds(env=env) == 145.0
    assert tools_env.get_web_search_provider_override(env=env) == "tinyfish"


def test_tinyfish_env_registry_and_provider_enum_are_registered() -> None:
    specs = {item.name: item for item in get_env_var_specs()}

    assert specs["OPENMINION_WEB_SEARCH_PROVIDER"].value_type == (
        "enum(auto|brave|tavily|serpapi|firecrawl|serper|tinyfish)"
    )
    assert specs["TINYFISH_API_KEY"].owner == "tools/tinyfish providers"
    assert specs["TINYFISH_SEARCH_API_URL"].default == "https://api.search.tinyfish.ai"
    assert specs["TINYFISH_SEARCH_TIMEOUT_SECONDS"].default == "20.0"
    assert specs["TINYFISH_FETCH_API_URL"].default == "https://api.fetch.tinyfish.ai"
    assert specs["TINYFISH_FETCH_TIMEOUT_SECONDS"].default == "150.0"
