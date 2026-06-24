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


def test_modules_directory_has_no_forbidden_service_or_api_imports():
    hits: list[str] = []
    for path in _validator.MODULES_DIR.rglob("*.py"):
        hits.extend(_validator.scan_file(path))
    assert not hits, (
        "Forbidden modules → services/api imports detected.\n"
        "Each new violation must either be fixed (preferred) or added to "
        "`scripts/validate/import_boundaries.py:EXCLUDED_MODULE_FILES` with "
        "an explicit rationale comment (see CTCR/MSB tracker discipline).\n\n"
        + "\n".join(hits)
    )


def test_validator_rejects_a_newly_introduced_violation(tmp_path: pathlib.Path):
    test_file = _validator.MODULES_DIR / "_msb_06_injection_probe.py"
    try:
        test_file.write_text(
            '"""Temporary MSB-06 injection probe."""\n'
            "from openminion.services.runtime.cron_resume.handler import (\n"
            "    schedule_backoff_resume,\n"
            ")\n"
            "_ = schedule_backoff_resume\n",
            encoding="utf-8",
        )
        hits = _validator.scan_file(test_file)
        assert hits, (
            "MSB-06: validator failed to detect a deliberately injected "
            "`openminion.services.*` import in a fresh modules-tree file. "
            "The boundary guard is broken."
        )
        # The validator's hit format is `{rel_path}:{line}: {matched_pattern}`
        # where `matched_pattern` is the regex match itself (`from openminion.services.`),
        # not the full import line. Assert the injected file's path appears.
        assert any("_msb_06_injection_probe.py" in h for h in hits), (
            f"Expected the probe filename in at least one hit: {hits!r}"
        )
    finally:
        if test_file.exists():
            test_file.unlink()


def test_validator_allowlist_is_in_sync_with_existing_violations():
    stale: list[str] = []
    for rel in _validator.EXCLUDED_MODULE_FILES:
        path = REPO_ROOT / rel
        if not path.exists():
            stale.append(f"{rel} (file does not exist)")
            continue
        # Temporarily remove from allowlist to see if it would otherwise hit
        original = _validator.EXCLUDED_MODULE_FILES
        try:
            _validator.EXCLUDED_MODULE_FILES = set(original) - {rel}
            hits = _validator.scan_file(path)
        finally:
            _validator.EXCLUDED_MODULE_FILES = original
        if not hits:
            stale.append(
                f"{rel} (on allowlist but no forbidden import remains; "
                "remove the entry)"
            )
    assert not stale, "Stale `EXCLUDED_MODULE_FILES` entries detected:\n" + "\n".join(
        stale
    )
