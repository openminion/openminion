from __future__ import annotations

from types import SimpleNamespace

from openminion.base.config.env import resolve_environment_config_with_explicit_env
from openminion.modules.tool.runtime.registrar import ToolRegisterContext
from openminion.modules.tool.registry import ToolRegistry
from openminion.tools.search import plugin as search_plugin
from openminion.tools.search.providers.tinyfish import REGISTRAR
from openminion.tools.search.providers.tinyfish.provider import TinyFishSearchProvider


def setup_function() -> None:
    search_plugin._PROVIDERS.clear()
    search_plugin._PROVIDER_ORDER.clear()


def teardown_function() -> None:
    search_plugin._PROVIDERS.clear()
    search_plugin._PROVIDER_ORDER.clear()


def test_registrar_is_provider_only_with_empty_manifest() -> None:
    # `REGISTRAR` is now built from `SEARCH_TINYFISH_FAMILY`
    # via `build_registrar(...)`. Behavior parity with the old
    # hand-written `SearchTinyFishRegistrar` is what this test pins.
    manifest = REGISTRAR.get_manifest(
        ToolRegisterContext(module_id="search.tinyfish", config=None)
    )

    assert REGISTRAR.is_provider_only is True
    assert REGISTRAR.module_id == "search.tinyfish"
    assert manifest.module_id == "search.tinyfish"
    assert manifest.model_tools == ()
    assert manifest.runtime_bindings == ()


def test_registrar_registers_tinyfish_provider_into_shared_search_map() -> None:
    registry = ToolRegistry()

    REGISTRAR.register(registry)

    assert search_plugin.list_provider_ids() == ("tinyfish",)


def test_healthcheck_is_key_based_only() -> None:
    provider = TinyFishSearchProvider()

    assert (
        provider.healthcheck(
            ctx=SimpleNamespace(
                env=resolve_environment_config_with_explicit_env(
                    {"TINYFISH_API_KEY": "tinyfish-key"}
                )
            )
        )
        is True
    )
    assert (
        provider.healthcheck(
            ctx=SimpleNamespace(
                env=resolve_environment_config_with_explicit_env(
                    {"TINYFISH_API_KEY": ""}
                )
            )
        )
        is False
    )
