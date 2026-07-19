from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


def _load_validator_module():
    script_path = (
        Path(__file__).resolve().parents[2] / "scripts" / "validate/base_charter.py"
    )
    spec = importlib.util.spec_from_file_location("validate_base_charter", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = _load_validator_module()


def test_validate_base_charter_passes_live_tree(capsys) -> None:
    rc = MODULE.main()
    captured = capsys.readouterr().out.strip().splitlines()[-1]
    payload = json.loads(captured)
    assert rc == 0
    assert payload["ok"] is True


def test_validate_root_layout_flags_unexpected_subpackage(tmp_path: Path) -> None:
    for file_name in MODULE.ALLOWED_ROOT_FILES:
        (tmp_path / file_name).write_text("", encoding="utf-8")
    for dirname in MODULE.ALLOWED_TOP_LEVEL_DIRS:
        (tmp_path / dirname).mkdir()
        (tmp_path / dirname / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "telemetry").mkdir()

    errors = MODULE.validate_root_layout(tmp_path)

    assert errors
    assert any("telemetry" in error for error in errors)


def _write_source(root: Path, relative_path: str, source: str) -> None:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


@pytest.mark.parametrize("area", ["api", "cli", "modules", "services", "tools"])
def test_upward_import_validator_rejects_every_higher_area(
    tmp_path: Path, area: str
) -> None:
    _write_source(
        tmp_path,
        "config/example.py",
        f"from openminion.{area}.example import Contract\n",
    )

    errors = MODULE.validate_upward_imports(tmp_path, baseline={})

    assert errors == [
        f"New upward import from base: config/example.py:1: openminion.{area}.example"
    ]


def test_upward_import_validator_finds_type_only_and_lazy_imports(
    tmp_path: Path,
) -> None:
    _write_source(
        tmp_path,
        "types.py",
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    from openminion.services.stats import RunStats\n\n"
        "def load_contract():\n"
        "    from openminion.modules.context import Contract\n"
        "    return Contract\n",
    )

    imports, parse_errors = MODULE.find_upward_imports(tmp_path)

    assert not parse_errors
    assert imports == [
        MODULE.UpwardImport("types.py", 3, "openminion.services.stats"),
        MODULE.UpwardImport("types.py", 6, "openminion.modules.context"),
    ]


def test_temporary_baseline_is_exact_and_rejects_growth(tmp_path: Path) -> None:
    _write_source(
        tmp_path,
        "config/contracts.py",
        "from openminion.modules.context import Contract\n",
    )
    edge = MODULE.UpwardImport("config/contracts.py", 1, "openminion.modules.context")

    assert MODULE.validate_upward_imports(tmp_path, baseline={edge: "BFBS-03"}) == []

    _write_source(
        tmp_path,
        "config/extra.py",
        "from openminion.services.runtime import Runtime\n",
    )
    errors = MODULE.validate_upward_imports(tmp_path, baseline={edge: "BFBS-03"})

    assert errors == [
        "New upward import from base: config/extra.py:1: openminion.services.runtime"
    ]


def test_temporary_baseline_rejects_stale_entries(tmp_path: Path) -> None:
    stale = MODULE.UpwardImport("types.py", 1, "openminion.services.stats")

    errors = MODULE.validate_upward_imports(tmp_path, baseline={stale: "BFBS-02"})

    assert errors == [
        "Stale base upward-import baseline entry: "
        "types.py:1: openminion.services.stats (BFBS-02)"
    ]


@pytest.mark.parametrize(
    "relative_path",
    ["config/tool_execution_policy.py", "config/shared.py"],
)
def test_exact_owner_inventory_rejects_unreviewed_base_growth(
    tmp_path: Path, relative_path: str
) -> None:
    _write_source(tmp_path, relative_path, "class FeaturePolicy:\n    pass\n")

    errors = MODULE.validate_complexity_ratchets(tmp_path, baseline={})

    assert errors == [
        f"Unreviewed Base Python owner: {relative_path}; "
        "apply the local -> area -> Base owner ladder"
    ]


@pytest.mark.parametrize(
    ("metric", "budget_value"),
    [("loc", 1), ("callables", 0), ("max_callable_loc", 1), ("max_parameters", 1)],
)
def test_complexity_ratchets_reject_every_metric_increase(
    tmp_path: Path, metric: str, budget_value: int
) -> None:
    relative_path = "config/example.py"
    _write_source(
        tmp_path, relative_path, "def example(first, second):\n    return first\n"
    )
    measured, errors = MODULE.measure_complexity(tmp_path)
    assert not errors
    baseline = {
        relative_path: measured[relative_path]._replace(**{metric: budget_value})
    }

    errors = MODULE.validate_complexity_ratchets(tmp_path, baseline=baseline)

    assert errors == [
        f"Base {metric} ratchet increased: {relative_path}: "
        f"{getattr(measured[relative_path], metric)} > {budget_value}"
    ]


def test_complexity_ratchet_requires_lowering_stale_budget(tmp_path: Path) -> None:
    relative_path = "config/example.py"
    _write_source(tmp_path, relative_path, "VALUE = 1\n")
    baseline = {relative_path: MODULE.ComplexityBudget(2, 0, 0, 0)}

    errors = MODULE.validate_complexity_ratchets(tmp_path, baseline=baseline)

    assert errors == [
        "Stale Base loc ratchet: config/example.py: lower baseline from 2 to 1"
    ]


def test_complexity_ratchet_rejects_callable_and_file_ceilings(tmp_path: Path) -> None:
    callable_path = "config/long_method.py"
    file_path = "config/oversized.py"
    _write_source(
        tmp_path,
        callable_path,
        "def long_method():\n" + "    value = 1\n" * 100,
    )
    _write_source(tmp_path, file_path, "# catalog row\n" * 501)
    baseline, errors = MODULE.measure_complexity(tmp_path)
    assert not errors

    errors = MODULE.validate_complexity_ratchets(tmp_path, baseline=baseline)

    assert errors == [
        "Base callable LOC ceiling exceeded: config/long_method.py: 101 > 100",
        "Base file LOC ceiling exceeded: config/oversized.py: 501 > 500",
    ]


def test_complexity_ratchet_accepts_reviewed_mcp_catalog_exception(
    tmp_path: Path,
) -> None:
    relative_path = "config/mcp.py"
    _write_source(tmp_path, relative_path, "# declarative MCP row\n" * 501)
    baseline, errors = MODULE.measure_complexity(tmp_path)
    assert not errors

    assert (
        MODULE.validate_complexity_ratchets(
            tmp_path, baseline=baseline, total_loc_limit=1_000
        )
        == []
    )


def test_complexity_ratchet_rejects_area_loc_growth(tmp_path: Path) -> None:
    relative_path = "types.py"
    _write_source(tmp_path, relative_path, "VALUE = 1\n")
    baseline, errors = MODULE.measure_complexity(tmp_path)
    assert not errors

    errors = MODULE.validate_complexity_ratchets(
        tmp_path, baseline=baseline, total_loc_limit=0
    )

    assert errors == ["Base area LOC ceiling exceeded: 1 > 0"]
