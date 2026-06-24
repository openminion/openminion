from __future__ import annotations

from openminion.modules.tool.registry import ToolRegistry
from openminion.modules.tool.runtime.registrar import ToolRegisterContext
from openminion.tools.fetch.providers import provider_registry
from openminion.tools.fetch.providers.tinyfish import REGISTRAR


def test_registrar_is_provider_only_with_empty_manifest() -> None:
    manifest = REGISTRAR.get_manifest(
        ToolRegisterContext(module_id="fetch_tinyfish", config=None)
    )

    assert REGISTRAR.is_provider_only is True
    assert REGISTRAR.module_id == "fetch_tinyfish"
    assert manifest.module_id == "fetch_tinyfish"
    assert manifest.model_tools == ()
    assert manifest.runtime_bindings == ()


def test_register_registers_tinyfish_provider(monkeypatch) -> None:
    del monkeypatch

    REGISTRAR.register(ToolRegistry())

    assert "tinyfish" in provider_registry().list_names()
