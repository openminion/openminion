from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def _load_validator_module():
    script_path = (
        Path(__file__).resolve().parents[2]
        / "scripts"
        / "validate/self_improvement_contract.py"
    )
    spec = importlib.util.spec_from_file_location(
        "validate_self_improvement_contract",
        script_path,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = _load_validator_module()


def test_validator_tracks_canonical_improvement_owner() -> None:
    assert MODULE.SELF_IMPROVEMENT_PATH.relative_to(MODULE.SRC_ROOT).as_posix() == (
        "modules/brain/runtime/improvement/notes.py"
    )


def test_validator_passes_live_tree(capsys) -> None:
    rc = MODULE.main()
    captured = capsys.readouterr().out.strip().splitlines()[-1]
    payload = json.loads(captured)
    assert rc == 0
    assert payload["ok"] is True


def test_validator_flags_trigger_tokens_outside_allowed_surface(tmp_path: Path) -> None:
    src_root = tmp_path / "src" / "openminion"
    surface = src_root / "modules" / "brain"
    surface.mkdir(parents=True, exist_ok=True)
    (surface / "bad_surface.py").write_text(
        "def broken(payload):\n    return payload.get('trigger_tokens', [])\n",
        encoding="utf-8",
    )

    findings = MODULE.validate(src_root)

    assert findings
    assert any("trigger_tokens" in finding for finding in findings)


def test_validator_flags_nonzero_applied_count_write(tmp_path: Path) -> None:
    src_root = tmp_path / "src" / "openminion"
    surface = src_root / "services" / "agent"
    surface.mkdir(parents=True, exist_ok=True)
    (surface / "bad_runtime.py").write_text(
        "def broken(runtime):\n"
        "    runtime.self_improvement_metadata['improvement_notes_applied_count'] = '1'\n",
        encoding="utf-8",
    )

    findings = MODULE.validate(src_root)

    assert findings
    assert any("improvement_notes_applied_count" in finding for finding in findings)
