from __future__ import annotations

import os
from pathlib import Path


def artifact_root(tmp_path: Path) -> Path:
    raw = str(os.getenv("OPENMINION_TUI_FOCUS_E2E_ARTIFACT_ROOT", "")).strip()
    root = Path(raw).expanduser() if raw else tmp_path
    root.mkdir(parents=True, exist_ok=True)
    return root


def write_transcript(root: Path, name: str, transcript: str) -> Path:
    target = root / f"{name}.ansi.txt"
    target.write_text(transcript, encoding="utf-8")
    return target
