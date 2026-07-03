from __future__ import annotations

import importlib

from openminion.modules.tool.contracts import (
    ALL_MODEL_TOOL_IDS,
    ALL_MODEL_TOOL_IDS_SET,
    ALL_RUNTIME_BINDING_IDS,
    ALL_RUNTIME_BINDING_IDS_SET,
    normalize_raw_model_tool_name,
)
from openminion.modules.tool.bootstrap import _TOOL_BOOTSTRAP_ENTRIES
from openminion.modules.tool.runtime.registrar import ToolRegisterContext
from openminion.modules.tool.runtime.registry_categories import (
    mapped_category_for_tool_name,
)
from openminion.modules.tool import build_default_tool_registry
from tests.test_tool_registry_manager import _get_bootstrap_manager


def test_normalize_raw_model_tool_name_handles_wrapper_prefixes() -> None:
    assert normalize_raw_model_tool_name("file.read") == "file.read"
    assert normalize_raw_model_tool_name("functions.FILE.READ") == "file.read"
    assert normalize_raw_model_tool_name("web.search") == "web.search"
    assert normalize_raw_model_tool_name("tool.list") == "tool.list"


def test_normalize_raw_model_tool_name_rejects_unknown_aliases() -> None:
    assert normalize_raw_model_tool_name("read_file") is None
    assert normalize_raw_model_tool_name("web_search") is None


def test_manager_resolves_registered_model_tool_ids() -> None:
    mgr = _get_bootstrap_manager()
    sample_ids = ("file.list_dir", "file.read", "exec.run", "web.search")
    for model_tool_id in sample_ids:
        runtime_binding_id = mgr.resolve_binding(model_tool_id)
        assert runtime_binding_id is not None, f"{model_tool_id} should resolve"
        assert runtime_binding_id.startswith("runtime."), (
            f"{model_tool_id} should map to runtime.*"
        )


def test_manager_runtime_candidates() -> None:
    mgr = _get_bootstrap_manager()
    candidates = mgr.runtime_candidates("runtime.file.read")
    assert "file.read" in candidates


def test_registry_categories_are_canonical_only() -> None:
    canonical = mapped_category_for_tool_name("file.list_dir")
    assert canonical is not None

    web_canonical = mapped_category_for_tool_name("web.search")
    assert web_canonical is not None
    assert mapped_category_for_tool_name("web_search") is None


def test_contract_constants_match_compiled_manifest_ids() -> None:
    mgr = _get_bootstrap_manager()
    model_to_binding = mgr.model_to_runtime_binding_map()

    compiled_model_ids = set(model_to_binding.keys())
    compiled_runtime_binding_ids = set(model_to_binding.values())

    assert compiled_model_ids == set(ALL_MODEL_TOOL_IDS_SET)
    assert compiled_runtime_binding_ids == set(ALL_RUNTIME_BINDING_IDS_SET)


def test_model_tool_ids_have_single_runtime_owner_with_available_registry_tools() -> (
    None
):
    mgr = _get_bootstrap_manager()
    registry = build_default_tool_registry()
    available_runtime_tools = set(registry.list().keys())
    dispatch_map = mgr.model_runtime_dispatch_map(
        available_runtime_tools=available_runtime_tools
    )
    compiled_model_ids = set(mgr.model_to_runtime_binding_map().keys())
    eligible_model_ids = []
    for model_tool_id in ALL_MODEL_TOOL_IDS:
        if model_tool_id not in compiled_model_ids:
            continue
        runtime_binding_id = mgr.resolve_binding(model_tool_id)
        if not runtime_binding_id:
            continue
        candidates = set(mgr.runtime_candidates(runtime_binding_id))
        if candidates & available_runtime_tools:
            eligible_model_ids.append(model_tool_id)
    missing = [
        model_tool_id
        for model_tool_id in eligible_model_ids
        if not str(
            (dispatch_map.get(model_tool_id) or {}).get("runtime_tool_name", "")
        ).strip()
    ]
    assert not missing, f"missing runtime owner for canonical model ids: {missing}"


def test_provider_only_registrars_do_not_own_runtime_binding_anchors() -> None:
    for entry in _TOOL_BOOTSTRAP_ENTRIES:
        if entry.kind != "tool":
            continue
        module = importlib.import_module(entry.module_name)
        registrar = getattr(module, "REGISTRAR", None)
        if not bool(getattr(registrar, "is_provider_only", False)):
            continue
        ctx = ToolRegisterContext(
            module_id=str(getattr(registrar, "module_id", "")),
            strict=False,
        )
        manifest = registrar.get_manifest(ctx)
        if manifest is None:
            continue
        assert tuple(getattr(manifest, "model_tools", ())) == ()
        assert tuple(getattr(manifest, "runtime_bindings", ())) == ()


def test_all_model_tool_ids_resolve_to_runtime_binding() -> None:
    mgr = _get_bootstrap_manager()
    missing = [mid for mid in ALL_MODEL_TOOL_IDS if not mgr.resolve_binding(mid)]
    assert not missing, (
        f"model tool IDs with no runtime binding after bootstrap: {missing}"
    )


def test_all_runtime_binding_ids_have_at_least_one_candidate() -> None:
    mgr = _get_bootstrap_manager()
    empty = [rid for rid in ALL_RUNTIME_BINDING_IDS if not mgr.runtime_candidates(rid)]
    assert not empty, f"runtime binding IDs with no candidates after bootstrap: {empty}"


def test_recently_added_search_provider_candidates_normalize_correctly() -> None:
    mgr = _get_bootstrap_manager()
    for candidate in (
        "search.serpapi.search",
        "search.firecrawl.search",
        "search.tinyfish.search",
    ):
        normalized = mgr.normalize_raw_name(candidate)
        assert normalized == "web.search", (
            f"{candidate!r} should normalize to 'web.search', got {normalized!r}"
        )
    # Confirm both are present in the binding's candidate list
    cands = mgr.runtime_candidates("runtime.web.search")
    assert "search.serpapi.search" in cands
    assert "search.firecrawl.search" in cands
    assert "search.tinyfish.search" in cands


def test_runtime_candidates_do_not_collide_across_model_tool_owners() -> None:
    mgr = _get_bootstrap_manager()
    dispatch_map = mgr.model_runtime_dispatch_map()
    owners: dict[str, set[str]] = {}
    for model_tool_id, payload in dispatch_map.items():
        for runtime_candidate in payload.get("runtime_candidates", []):
            candidate = str(runtime_candidate or "").strip()
            if not candidate:
                continue
            owners.setdefault(candidate, set()).add(str(model_tool_id))
    collisions = {
        runtime_candidate: sorted(owner_ids)
        for runtime_candidate, owner_ids in owners.items()
        if len(owner_ids) > 1
    }
    assert not collisions, f"runtime candidate collisions detected: {collisions}"
