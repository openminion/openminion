from __future__ import annotations

import importlib.util
import pathlib
import sys


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
VALIDATOR_PATH = REPO_ROOT / "scripts" / "validate" / "import_boundaries.py"


def _load_validator():
    spec = importlib.util.spec_from_file_location(
        "openminion_validate_import_boundaries", VALIDATOR_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_validator = _load_validator()


def test_live_tree_has_no_reverse_layer_imports():
    hits: list[str] = []
    for path in _validator.MODULES_DIR.rglob("*.py"):
        hits.extend(_validator.scan_file(path))
    for path in _validator.SERVICES_DIR.rglob("*.py"):
        hits.extend(_validator.scan_file(path))
    assert not hits, "Forbidden reverse layer imports detected.\n\n" + "\n".join(hits)


def test_validator_rejects_indented_and_type_only_module_to_service_imports(
    tmp_path: pathlib.Path,
):
    test_file = tmp_path / "module_probe.py"
    test_file.write_text(
        "if TYPE_CHECKING:\n"
        "    from openminion.services.runtime import OpenMinionRuntime\n"
        "def load():\n"
        "    import openminion.services.cron\n",
        encoding="utf-8",
    )

    hits = _validator.scan_file(test_file, layer="modules")

    assert len(hits) == 2


def test_validator_rejects_type_only_and_lazy_service_to_api_imports(
    tmp_path: pathlib.Path,
):
    test_file = tmp_path / "service_probe.py"
    test_file.write_text(
        "if TYPE_CHECKING:\n"
        "    from openminion.api.runtime import APIRuntime\n"
        "def load():\n"
        "    import openminion.api.constants\n",
        encoding="utf-8",
    )

    hits = _validator.scan_file(test_file, layer="services")

    assert len(hits) == 2


def test_validator_rejects_type_only_and_lazy_service_to_cli_imports(
    tmp_path: pathlib.Path,
):
    test_file = tmp_path / "service_probe.py"
    test_file.write_text(
        "if TYPE_CHECKING:\n"
        "    from openminion.cli.main import main\n"
        "def load():\n"
        "    import openminion.cli.commands.context_cleanup\n",
        encoding="utf-8",
    )

    hits = _validator.scan_file(test_file, layer="services")

    assert len(hits) == 2


def test_validator_accepts_canonical_dependency_directions(tmp_path: pathlib.Path):
    module_file = tmp_path / "module_ok.py"
    module_file.write_text(
        "from openminion.modules.task import TaskCtl\n",
        encoding="utf-8",
    )
    service_file = tmp_path / "service_ok.py"
    service_file.write_text(
        "from openminion.modules.task import TaskCtl\n",
        encoding="utf-8",
    )

    assert _validator.scan_file(module_file, layer="modules") == []
    assert _validator.scan_file(service_file, layer="services") == []


def test_import_boundary_validator_includes_base_upward_imports(
    tmp_path: pathlib.Path,
):
    base_file = tmp_path / "config" / "lazy_feature.py"
    base_file.parent.mkdir(parents=True)
    base_file.write_text(
        "def load():\n"
        "    from openminion.modules.task import TaskCtl\n"
        "    return TaskCtl\n",
        encoding="utf-8",
    )

    assert _validator.scan_base(tmp_path) == [
        "New upward import from base: config/lazy_feature.py:2: openminion.modules.task"
    ]
