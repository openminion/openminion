from __future__ import annotations

import json

from .cli_entrypoint_paths import (
    module_cli_entrypoint_path,
    module_cli_fixture_path,
    openminion_modules_root,
)


def test_module_cli_exemptions_policy_is_machine_readable_and_aligned() -> None:
    policy_path = module_cli_fixture_path("cli_exemptions.json")
    assert policy_path.exists()

    payload = json.loads(policy_path.read_text(encoding="utf-8"))
    assert payload.get("schema_version") == 1
    assert payload.get("policy") == "module_cli_exemptions"

    modules_root = openminion_modules_root()
    exemptions = payload.get("exemptions")
    assert isinstance(exemptions, list)

    _ALLOWED_EXEMPT_MODULES = {"runtime", "prompting"}

    module_names = set()
    for item in exemptions:
        assert isinstance(item, dict)
        name = str(item.get("module", "")).strip()
        classification = str(item.get("classification", "")).strip()
        reason = str(item.get("reason", "")).strip()
        assert name
        assert classification == "cli_exempt_internal"
        assert len(reason.split()) >= 5
        assert name not in module_names
        module_names.add(name)

        module_dir = modules_root / name
        assert (module_dir / "__init__.py").exists()
        assert module_cli_entrypoint_path(module_dir) is None
        assert not (module_dir / "__main__.py").exists()

    assert module_names == _ALLOWED_EXEMPT_MODULES
