from __future__ import annotations

import json

from .cli_entrypoint_paths import (
    module_cli_entrypoint_path,
    module_cli_fixture_path,
    openminion_modules_root,
)


def test_cli_bootstrap_policy_noop_modules_are_explicit_and_valid() -> None:
    policy_path = module_cli_fixture_path("cli_bootstrap_policy.json")
    payload = json.loads(policy_path.read_text(encoding="utf-8"))
    no_ops = payload.get("no_op_modules")
    assert isinstance(no_ops, list)
    module_names = [entry.get("module") for entry in no_ops if isinstance(entry, dict)]
    assert module_names == ["llm"]
    assert len(module_names) == len(set(module_names))

    modules_root = openminion_modules_root()
    for entry in no_ops:
        assert isinstance(entry, dict)
        assert entry.get("policy") == "bootstrap_noop"
        reason = str(entry.get("reason") or "").strip()
        assert len(reason) >= 20
        module = str(entry["module"])
        cli_path = module_cli_entrypoint_path(modules_root / module)
        assert cli_path is not None, (
            f"missing CLI entrypoint for no-op module: {module}"
        )
        text = cli_path.read_text(encoding="utf-8")
        assert "apply_home_data_root_env(" not in text
        assert 'os.environ["OPENMINION_HOME"]' not in text
        assert 'os.environ["OPENMINION_DATA_ROOT"]' not in text
