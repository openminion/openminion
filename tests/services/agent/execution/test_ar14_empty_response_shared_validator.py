from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openminion.base.constants import STATE_KEY_FINALIZATION_STATUS
from openminion.services.agent.execution.validators import is_empty_provider_response

REPO_ROOT = Path(__file__).resolve().parents[5]
REQUIRED_LANE_FILE = (
    REPO_ROOT
    / "openminion"
    / "src"
    / "openminion"
    / "services"
    / "agent"
    / "execution"
    / "required_lane"
    / "post_execution.py"
)
UNFORCED_LANE_FILE = (
    REPO_ROOT
    / "openminion"
    / "src"
    / "openminion"
    / "services"
    / "agent"
    / "execution"
    / "unforced_lane"
    / "loop.py"
)


@dataclass
class _FakeResponse:
    text: str = ""
    tool_calls: list[Any] = field(default_factory=list)
    finalization_status: Any = None


def _attach_finalization(response: _FakeResponse, payload: Any) -> _FakeResponse:
    setattr(response, STATE_KEY_FINALIZATION_STATUS, payload)
    return response


def test_predicate_fires_on_tri_empty() -> None:
    assert is_empty_provider_response(_FakeResponse(text="", tool_calls=[])) is True


def test_predicate_skips_whitespace_only_text() -> None:
    assert (
        is_empty_provider_response(_FakeResponse(text="   \t\n", tool_calls=[])) is True
    )


def test_predicate_skips_visible_text() -> None:
    assert (
        is_empty_provider_response(_FakeResponse(text="answer", tool_calls=[])) is False
    )


def test_predicate_skips_when_tool_calls_present() -> None:
    assert (
        is_empty_provider_response(_FakeResponse(text="", tool_calls=[object()]))
        is False
    )


def test_predicate_skips_when_finalization_status_present() -> None:
    resp = _attach_finalization(
        _FakeResponse(text="", tool_calls=[]), {"status": "complete"}
    )
    assert is_empty_provider_response(resp) is False


def test_predicate_treats_falsy_finalization_as_absent() -> None:
    resp = _attach_finalization(_FakeResponse(text="", tool_calls=[]), {})
    assert is_empty_provider_response(resp) is True


def _file_imports_shared_validator(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if not (module.endswith("validators") or module == "..validators"):
                continue
            for alias in node.names:
                if alias.name == "is_empty_provider_response":
                    return True
    return False


def _file_defines_local_predicate(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    for node in ast.iter_child_nodes(tree):
        if (
            isinstance(node, ast.FunctionDef)
            and node.name == "_is_empty_provider_response"
        ):
            return True
    return False


def test_required_lane_imports_shared_validator() -> None:
    assert REQUIRED_LANE_FILE.exists(), f"missing {REQUIRED_LANE_FILE}"
    assert _file_imports_shared_validator(REQUIRED_LANE_FILE), (
        "AR-14: required_lane/post_execution.py must import "
        "`is_empty_provider_response` from the shared validators module"
    )


def test_required_lane_no_longer_defines_local_predicate() -> None:
    assert not _file_defines_local_predicate(REQUIRED_LANE_FILE), (
        "AR-14: the local `_is_empty_provider_response` def in "
        "required_lane/post_execution.py should be gone — only the alias "
        "to the shared validator remains."
    )


def test_unforced_lane_imports_shared_validator() -> None:
    assert UNFORCED_LANE_FILE.exists(), f"missing {UNFORCED_LANE_FILE}"
    assert _file_imports_shared_validator(UNFORCED_LANE_FILE), (
        "AR-14: unforced_lane/loop.py must import "
        "`is_empty_provider_response` from the shared validators module"
    )


def test_unforced_lane_uses_predicate_in_loop_body() -> None:
    text = UNFORCED_LANE_FILE.read_text(encoding="utf-8")
    assert "is_empty_provider_response(response)" in text, (
        "AR-14: unforced_lane/loop.py must call "
        "`is_empty_provider_response(response)` before returning "
        "`model_final_response`"
    )


def test_unforced_lane_has_empty_provider_response_builder() -> None:
    metadata_file = (
        REPO_ROOT
        / "openminion"
        / "src"
        / "openminion"
        / "services"
        / "agent"
        / "execution"
        / "unforced_lane"
        / "metadata.py"
    )
    text = metadata_file.read_text(encoding="utf-8")
    assert "def empty_provider_response_response(" in text
    assert "empty_provider_response" in text
    assert "EMPTY_PROVIDER_RESPONSE" in text
