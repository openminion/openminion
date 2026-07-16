from __future__ import annotations

import importlib
from pathlib import Path

CLI_ROOT = Path(__file__).resolve().parents[2] / "src" / "openminion" / "cli"
IMPORT_SURFACE = [
    ("openminion.cli.main", "main"),
    ("openminion.cli.config", "load_cli_config"),
    ("openminion.cli.constants", "CLI_DEFAULT_THEME_VARIANT"),
    ("openminion.cli.bootstrap.loader", "load_config"),
    ("openminion.cli.bootstrap.paths", "CLI_IDENTITY_DB_FILENAME"),
    ("openminion.cli.parser", "build_parser"),
    ("openminion.cli.parser.flags", "add_json_output_flag"),
    ("openminion.cli.parser.contracts", "CLI_INTERFACE_VERSION"),
    ("openminion.cli.identity.provenance", "build_identity_provenance"),
    ("openminion.cli.identity.sync", "sync_cli_identity_profiles"),
    ("openminion.cli.transport.daemon_client", "daemon_request"),
    ("openminion.cli.presentation.styles", "StyleToken"),
]
PACKAGE_ALIAS_IMPORTS = {
    "parser": "build_parser",
    "contracts": "CLI_INTERFACE_VERSION",
    "styles": "StyleToken",
}
EXPECTED_ROOT_FILES = {
    "README.md",
    "__init__.py",
    "config.py",
    "constants.py",
    "main.py",
}
EXPECTED_GROUP_DIRS = {
    "bootstrap",
    "commands",
    "identity",
    "interactive",
    "parser",
    "presentation",
    "status",
    "transport",
}


def test_cli_layout_characterization_import_surface() -> None:
    for module_name, attr_name in IMPORT_SURFACE:
        module = importlib.import_module(module_name)
        assert hasattr(module, attr_name), f"{module_name} missing {attr_name}"


def test_cli_package_aliases_expose_legacy_modules() -> None:
    import openminion.cli as cli_pkg

    for legacy_name, attr_name in PACKAGE_ALIAS_IMPORTS.items():
        module = getattr(cli_pkg, legacy_name)
        assert hasattr(module, attr_name), f"alias {legacy_name} missing {attr_name}"
        assert importlib.import_module(f"openminion.cli.{legacy_name}") is module


def test_cli_root_files_match_post_clc_layout_contract() -> None:
    root_files = {path.name for path in CLI_ROOT.iterdir() if path.is_file()}
    assert root_files == EXPECTED_ROOT_FILES


def test_cli_grouped_helper_directories_exist() -> None:
    top_level_dirs = {
        path.name
        for path in CLI_ROOT.iterdir()
        if path.is_dir() and path.name != "__pycache__"
    }
    assert EXPECTED_GROUP_DIRS.issubset(top_level_dirs)
