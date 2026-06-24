from __future__ import annotations

from openminion.base.errors import (
    ErrorInfo,
    error_info_from_exception,
    error_info_from_mapping,
)
from openminion.modules.a2a.errors import A2AError
from openminion.modules.brain.interfaces import BrainRuntimeError
from openminion.modules.llm.errors import LLMCtlError
from openminion.modules.memory.errors import InvalidArgumentError
from openminion.modules.registry.errors import AgentRegError
from openminion.modules.retrieve.errors import RetrieveCtlError
from openminion.modules.skill.errors import SkillError
from openminion.modules.tool.errors import ToolRuntimeError


def test_error_adapter_import_smoke() -> None:
    info = ErrorInfo(code="INVALID_ARGUMENT", message="bad")
    assert info.to_dict() == {
        "code": "INVALID_ARGUMENT",
        "message": "bad",
        "details": {},
    }


def test_error_info_from_exception_covers_first_wave_domain_types() -> None:
    cases = [
        (
            SkillError("INVALID_ARGUMENT", "skill bad", {"field": "name"}),
            "INVALID_ARGUMENT",
            "skill bad",
            {"field": "name"},
            "skill",
        ),
        (
            ToolRuntimeError("POLICY_DENIED", "blocked", {"rule": "paths"}),
            "POLICY_DENIED",
            "blocked",
            {"rule": "paths"},
            "tool",
        ),
        (
            LLMCtlError("AUTH_ERROR", "bad key", {"provider": "stub"}),
            "AUTH_ERROR",
            "bad key",
            {"provider": "stub"},
            "llm",
        ),
        (
            InvalidArgumentError("memory bad", details={"field": "scope"}),
            "INVALID_ARGUMENT",
            "memory bad",
            {"field": "scope"},
            "memory",
        ),
        (
            BrainRuntimeError("NO_PLAN", "missing plan", {"mode": "plan"}),
            "NO_PLAN",
            "missing plan",
            {"mode": "plan"},
            "brain",
        ),
        (
            A2AError("INVALID_ARGUMENT", "bad route", {"agent": "echo"}),
            "INVALID_ARGUMENT",
            "bad route",
            {"agent": "echo"},
            "a2a",
        ),
        (
            RetrieveCtlError("INVALID_ARGUMENT", "empty text"),
            "INVALID_ARGUMENT",
            "empty text",
            {},
            "retrieve",
        ),
        (
            AgentRegError("NOT_FOUND", "agent missing", {"agent_id": "demo"}),
            "NOT_FOUND",
            "agent missing",
            {"agent_id": "demo"},
            "registry",
        ),
    ]

    for error, code, message, details, namespace in cases:
        info = error_info_from_exception(error)
        assert info.code == code
        assert info.message == message
        assert info.details == details
        assert info.namespace == namespace


def test_error_info_from_mapping_normalizes_singular_detail() -> None:
    info = error_info_from_mapping(
        {"code": "INVALID_ARGUMENT", "message": "bad payload", "detail": {"x": 1}}
    )
    assert info.code == "INVALID_ARGUMENT"
    assert info.message == "bad payload"
    assert info.details == {"x": 1}


def test_error_info_from_exception_falls_back_for_generic_exception() -> None:
    info = error_info_from_exception(RuntimeError("boom"))
    assert info.code == "INTERNAL_ERROR"
    assert info.message == "boom"
    assert info.details == {}
