from __future__ import annotations

import os
from pathlib import Path


_FRAMEWORK_ROOT = Path(__file__).resolve().parents[6]
_DEFAULT_ARTIFACT_ROOT = _FRAMEWORK_ROOT / "workspace-tmp" / "openminion-tui-focus-e2e"


def artifact_root(tmp_path: Path) -> Path:
    raw = str(os.getenv("OPENMINION_TUI_FOCUS_E2E_ARTIFACT_ROOT", "")).strip()
    if raw:
        root = Path(raw).expanduser()
    else:
        # Keep the default artifact/scratch root inside the shared workspace so
        # live file tools can write to scenario-owned scratch paths without
        # tripping the workspace-root guard.
        root = _DEFAULT_ARTIFACT_ROOT / tmp_path.name
    root.mkdir(parents=True, exist_ok=True)
    return root


def write_transcript(root: Path, name: str, transcript: str) -> Path:
    target = root / f"{name}.ansi.txt"
    target.write_text(transcript, encoding="utf-8")
    return target
