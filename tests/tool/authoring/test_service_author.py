from __future__ import annotations

from ._helpers import build_service


def _base_args() -> dict[str, object]:
    return {
        "name": "adder",
        "description": "Add two integers",
        "source_code": "def adder(x, y):\n    return x + y\n",
        "unit_tests_source": "def test_add():\n    assert 1 + 1 == 2\n",
        "args_schema": {
            "type": "object",
            "properties": {"x": {"type": "integer"}, "y": {"type": "integer"}},
            "required": ["x", "y"],
        },
        "returns_schema": {"type": "integer"},
        "requirements": [],
        "dependencies": [],
        "proposed_scope_tier": "POWER_USER",
    }


def test_author_draft_persists_row_and_audit(tmp_path) -> None:
    service = build_service(tmp_path)
    try:
        result = service.author_draft(
            _base_args(), agent_id="agent-1", session_id="sess-1"
        )
        assert result["ok"] is True
        draft = service.get_draft(str(result["draft_id"]))
        assert draft is not None
        assert draft.status == "drafted"
        events = service._store.list_audit_events(target_id=str(result["draft_id"]))  # noqa: SLF001
        assert events[-1].event_type == "tool_authoring.drafted"
    finally:
        service.close()


def test_author_draft_rejects_invalid_schema(tmp_path) -> None:
    service = build_service(tmp_path)
    try:
        args = _base_args()
        args["args_schema"] = {"properties": "bad"}
        result = service.author_draft(args)
        assert result["error"]["code"] == "INVALID_SCHEMA"
    finally:
        service.close()


def test_author_draft_rejects_invalid_source(tmp_path) -> None:
    service = build_service(tmp_path)
    try:
        args = _base_args()
        args["source_code"] = "def adder(:\n    pass\n"
        result = service.author_draft(args)
        assert result["error"]["code"] == "INVALID_SOURCE"
    finally:
        service.close()


def test_author_draft_rejects_missing_required_args(tmp_path) -> None:
    service = build_service(tmp_path)
    try:
        args = _base_args()
        args["source_code"] = "def adder(x):\n    return x\n"
        result = service.author_draft(args)
        assert result["error"]["code"] == "SIGNATURE_MISMATCH"
    finally:
        service.close()


def test_author_draft_rejects_disallowed_dependency(tmp_path) -> None:
    service = build_service(tmp_path, allowed_dependencies={"requests"})
    try:
        args = _base_args()
        args["source_code"] = "import pandas\n\ndef adder(x, y):\n    return x + y\n"
        args["dependencies"] = ["pandas"]
        result = service.author_draft(args)
        assert result["error"]["code"] == "DEP_NOT_ALLOWED"
    finally:
        service.close()
