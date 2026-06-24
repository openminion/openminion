from __future__ import annotations

from pathlib import Path


def openminion_repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def openminion_modules_root() -> Path:
    return openminion_repo_root() / "src" / "openminion" / "modules"


def openminion_examples_modules_root() -> Path:
    return openminion_repo_root() / "examples" / "modules"


def module_cli_fixture_root() -> Path:
    return openminion_repo_root() / "tests" / "runtime" / "fixtures" / "module_cli"


def module_cli_fixture_path(filename: str) -> Path:
    return module_cli_fixture_root() / filename


def module_cli_entrypoint_path(module_dir: Path) -> Path | None:
    file_entrypoint = module_dir / "cli.py"
    if file_entrypoint.exists():
        return file_entrypoint

    package_entrypoint = module_dir / "cli" / "__init__.py"
    if package_entrypoint.exists():
        try:
            text = package_entrypoint.read_text(encoding="utf-8")
        except OSError:
            return package_entrypoint
        for token, fallback in (
            ("from ._app import *", "_app.py"),
            ("from ._main import *", "_main.py"),
            ("from .app import *", "app.py"),
        ):
            if token in text:
                candidate = module_dir / "cli" / fallback
                if candidate.exists():
                    return candidate
        return package_entrypoint

    return None


def has_module_cli_entrypoint(module_dir: Path) -> bool:
    return module_cli_entrypoint_path(module_dir) is not None


def module_cli_shim_path(module_dir: Path) -> Path | None:
    file_entrypoint = module_dir / "cli.py"
    if file_entrypoint.exists():
        return file_entrypoint
    package_entrypoint = module_dir / "cli" / "__init__.py"
    if package_entrypoint.exists():
        return package_entrypoint
    return None
