import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


@pytest.fixture(autouse=True)
def _skill_data_root_alignment(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENMINION_DATA_ROOT", str(tmp_path))
