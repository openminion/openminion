from __future__ import annotations

from openminion.base.errors import (
    ENVELOPE_ERROR_CODES,
    DependencyCycleError,
    DependencyFailedError,
    DuplicateCallIdError,
    EnvelopeError,
    InvalidCallShapeError,
    InvalidEnvelopeShapeError,
    InvalidEnvelopeVersionError,
    InvalidResultShapeError,
    InvalidToolArgumentsError,
    UnknownDependencyError,
    UnknownToolNameError,
)
from openminion.modules.llm.providers.envelope_v2 import (
    CONTRACT_VERSION_V2,
    DuplicateCallIdParseError,
    EnvelopeParseError,
    parse_tool_call_envelope_v2,
)


def test_all_eleven_catalog_classes_importable() -> None:
    classes = [
        InvalidEnvelopeShapeError,
        InvalidEnvelopeVersionError,
        InvalidCallShapeError,
        InvalidResultShapeError,
        DuplicateCallIdError,
        UnknownToolNameError,
        InvalidToolArgumentsError,
        UnknownDependencyError,
        DependencyCycleError,
        DependencyFailedError,
    ]
    assert len(classes) == 10
    for cls in classes:
        assert issubclass(cls, EnvelopeError), (
            f"{cls.__name__} must inherit from EnvelopeError"
        )
        assert isinstance(cls.code, str) and cls.code, (
            f"{cls.__name__} must set a non-empty `code` class attribute"
        )


def test_each_class_carries_documented_code_string() -> None:
    expected = {
        InvalidEnvelopeShapeError: "INVALID_ENVELOPE_SHAPE",
        InvalidEnvelopeVersionError: "INVALID_ENVELOPE_VERSION",
        InvalidCallShapeError: "INVALID_CALL_SHAPE",
        InvalidResultShapeError: "INVALID_RESULT_SHAPE",
        DuplicateCallIdError: "DUPLICATE_CALL_ID",
        UnknownToolNameError: "UNKNOWN_TOOL_NAME",
        InvalidToolArgumentsError: "INVALID_TOOL_ARGUMENTS",
        UnknownDependencyError: "UNKNOWN_DEPENDENCY",
        DependencyCycleError: "DEPENDENCY_CYCLE",
        DependencyFailedError: "DEPENDENCY_FAILED",
    }
    for cls, code in expected.items():
        assert cls.code == code


def test_to_envelope_returns_documented_shape_with_details() -> None:
    err = DuplicateCallIdError(
        "duplicate call id 'x' at index 1",
        details={"duplicate_call_id": "x", "index": 1},
    )
    assert err.to_envelope() == {
        "code": "DUPLICATE_CALL_ID",
        "message": "duplicate call id 'x' at index 1",
        "details": {"duplicate_call_id": "x", "index": 1},
    }


def test_to_envelope_omits_no_keys_when_details_empty() -> None:
    err = UnknownToolNameError("unknown tool 'foo'")
    payload = err.to_envelope()
    assert payload == {
        "code": "UNKNOWN_TOOL_NAME",
        "message": "unknown tool 'foo'",
        "details": {},
    }


def test_to_envelope_isolates_details_copy() -> None:
    src = {"a": 1}
    err = UnknownDependencyError("missing dep", details=src)
    payload = err.to_envelope()
    payload["details"]["a"] = 999
    assert err.details == {"a": 1}, "to_envelope() must return a defensive copy"


def test_envelope_error_codes_frozenset_shape() -> None:
    assert isinstance(ENVELOPE_ERROR_CODES, frozenset)
    assert len(ENVELOPE_ERROR_CODES) == 10
    assert ENVELOPE_ERROR_CODES == {
        "INVALID_ENVELOPE_SHAPE",
        "INVALID_ENVELOPE_VERSION",
        "INVALID_CALL_SHAPE",
        "INVALID_RESULT_SHAPE",
        "DUPLICATE_CALL_ID",
        "UNKNOWN_TOOL_NAME",
        "INVALID_TOOL_ARGUMENTS",
        "UNKNOWN_DEPENDENCY",
        "DEPENDENCY_CYCLE",
        "DEPENDENCY_FAILED",
    }


def test_spec_numbered_count_is_eleven() -> None:
    numbered_codes = [
        "INVALID_ENVELOPE_SHAPE",
        "INVALID_ENVELOPE_VERSION",
        "INVALID_CALL_SHAPE",
        "INVALID_RESULT_SHAPE",
        "DUPLICATE_CALL_ID",
        "UNKNOWN_TOOL_NAME",
        "INVALID_TOOL_ARGUMENTS",
        "DUPLICATE_CALL_ID",
        "UNKNOWN_DEPENDENCY",
        "DEPENDENCY_CYCLE",
        "DEPENDENCY_FAILED",
    ]
    assert len(numbered_codes) == 11


def test_every_catalog_class_code_is_registered() -> None:
    classes = [
        InvalidEnvelopeShapeError,
        InvalidEnvelopeVersionError,
        InvalidCallShapeError,
        InvalidResultShapeError,
        DuplicateCallIdError,
        UnknownToolNameError,
        InvalidToolArgumentsError,
        UnknownDependencyError,
        DependencyCycleError,
        DependencyFailedError,
    ]
    for cls in classes:
        assert cls.code in ENVELOPE_ERROR_CODES


def _envelope_with_duplicate_call_ids() -> dict:
    return {
        "contract_version": CONTRACT_VERSION_V2,
        "request_id": "req-1",
        "session_id": "sess-1",
        "turn_id": "turn-1",
        "calls": [
            {"id": "a", "name": "tool.x", "arguments": {}},
            {"id": "a", "name": "tool.y", "arguments": {}},
        ],
    }


def test_parser_raises_typed_duplicate_call_id_error() -> None:
    try:
        parse_tool_call_envelope_v2(_envelope_with_duplicate_call_ids())
    except DuplicateCallIdParseError as exc:
        assert isinstance(exc, EnvelopeParseError)
        assert isinstance(exc, DuplicateCallIdError)
        assert exc.code == "DUPLICATE_CALL_ID"
        assert exc.details.get("duplicate_call_id") == "a"
        envelope = exc.to_envelope()
        assert envelope["code"] == "DUPLICATE_CALL_ID"
        assert envelope["details"]["duplicate_call_id"] == "a"
    else:
        raise AssertionError("expected DuplicateCallIdParseError on duplicate call id")


def test_legacy_envelope_parse_error_consumers_still_match() -> None:
    try:
        parse_tool_call_envelope_v2(_envelope_with_duplicate_call_ids())
    except EnvelopeParseError as exc:
        assert exc.code == "DUPLICATE_CALL_ID"
    else:
        raise AssertionError("expected EnvelopeParseError parent match on typed raise")
