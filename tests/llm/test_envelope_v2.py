from __future__ import annotations

import unittest

from openminion.modules.llm.providers.envelope_v2 import (
    CONTRACT_VERSION_V2,
    DEFAULT_EXECUTION_HINT,
    DEFAULT_RESULT_STATUS,
    DEFAULT_SOURCE,
    ERROR_DUPLICATE_CALL_ID,
    ERROR_INVALID_CALL_SHAPE,
    ERROR_INVALID_ENVELOPE_VERSION,
    ERROR_INVALID_ENVELOPE_SHAPE,
    ERROR_INVALID_RESULT_SHAPE,
    EnvelopeParseError,
    ToolCallEnvelopeV2,
    ToolCallV2,
    ToolResultEnvelopeV2,
    ToolResultV2,
    parse_tool_call_envelope_v2,
    parse_tool_result_envelope_v2,
    serialize_tool_call_envelope_v2,
    serialize_tool_result_envelope_v2,
)


def _valid_call_envelope_payload() -> dict:
    return {
        "contract_version": CONTRACT_VERSION_V2,
        "request_id": "req_1",
        "session_id": "sess_1",
        "turn_id": "turn_1",
        "calls": [
            {
                "id": "call_a",
                "name": "tavily.web.search",
                "arguments": {"query": "latest news"},
                "depends_on": [],
                "execution_hint": "auto",
                "source": "native",
            },
            {
                "id": "call_b",
                "name": "file.read",
                "arguments": {"path": "notes.md"},
                "depends_on": ["call_a"],
                "execution_hint": "auto",
                "source": "native",
            },
        ],
    }


def _valid_result_envelope_payload() -> dict:
    return {
        "contract_version": CONTRACT_VERSION_V2,
        "request_id": "req_1",
        "session_id": "sess_1",
        "turn_id": "turn_1",
        "results": [
            {
                "id": "call_a",
                "name": "tavily.web.search",
                "ok": True,
                "status": "success",
                "error_code": "",
                "error_message": "",
                "data": {"results": [{"url": "https://example.com"}]},
                "verified": True,
                "duration_ms": 182,
            },
        ],
    }


class ToolCallEnvelopeV2RoundTripTests(unittest.TestCase):
    def test_parse_full_payload_returns_typed_envelope(self) -> None:
        envelope = parse_tool_call_envelope_v2(_valid_call_envelope_payload())
        self.assertIsInstance(envelope, ToolCallEnvelopeV2)
        self.assertEqual(envelope.contract_version, CONTRACT_VERSION_V2)
        self.assertEqual(envelope.request_id, "req_1")
        self.assertEqual(envelope.session_id, "sess_1")
        self.assertEqual(envelope.turn_id, "turn_1")
        self.assertEqual(len(envelope.calls), 2)

        first, second = envelope.calls
        self.assertIsInstance(first, ToolCallV2)
        self.assertEqual(first.id, "call_a")
        self.assertEqual(first.name, "tavily.web.search")
        self.assertEqual(first.arguments, {"query": "latest news"})
        self.assertEqual(first.depends_on, [])
        self.assertEqual(first.execution_hint, "auto")
        self.assertEqual(first.source, "native")
        self.assertEqual(second.depends_on, ["call_a"])

    def test_round_trip_serialize_then_parse_is_idempotent(self) -> None:
        envelope = parse_tool_call_envelope_v2(_valid_call_envelope_payload())
        round_tripped = parse_tool_call_envelope_v2(
            serialize_tool_call_envelope_v2(envelope)
        )
        self.assertEqual(round_tripped, envelope)

    def test_serialize_emits_contract_field_layout(self) -> None:
        envelope = ToolCallEnvelopeV2(
            request_id="req_2",
            session_id="sess_2",
            turn_id="turn_2",
            calls=[
                ToolCallV2(
                    id="call_x",
                    name="weather.openmeteo.current",
                    arguments={"location": "sf"},
                ),
            ],
        )
        serialized = serialize_tool_call_envelope_v2(envelope)
        self.assertEqual(serialized["contract_version"], CONTRACT_VERSION_V2)
        self.assertEqual(serialized["request_id"], "req_2")
        self.assertEqual(serialized["session_id"], "sess_2")
        self.assertEqual(serialized["turn_id"], "turn_2")
        self.assertEqual(len(serialized["calls"]), 1)
        only_call = serialized["calls"][0]
        self.assertEqual(only_call["id"], "call_x")
        self.assertEqual(only_call["name"], "weather.openmeteo.current")
        self.assertEqual(only_call["arguments"], {"location": "sf"})
        self.assertEqual(only_call["depends_on"], [])
        self.assertEqual(only_call["execution_hint"], DEFAULT_EXECUTION_HINT)
        self.assertEqual(only_call["source"], DEFAULT_SOURCE)

    def test_parse_applies_field_defaults_for_optional_keys(self) -> None:
        payload = {
            "contract_version": CONTRACT_VERSION_V2,
            "request_id": "req_d",
            "session_id": "sess_d",
            "turn_id": "turn_d",
            "calls": [
                {
                    "id": "call_d",
                    "name": "time.now",
                    "arguments": {},
                    # `depends_on`, `execution_hint`, `source` intentionally absent.
                },
            ],
        }
        envelope = parse_tool_call_envelope_v2(payload)
        self.assertEqual(envelope.calls[0].depends_on, [])
        self.assertEqual(envelope.calls[0].execution_hint, DEFAULT_EXECUTION_HINT)
        self.assertEqual(envelope.calls[0].source, DEFAULT_SOURCE)


class ToolCallEnvelopeV2NegativePathTests(unittest.TestCase):
    def _assert_raises_with_code(
        self,
        payload: object,
        *,
        expected_code: str,
    ) -> EnvelopeParseError:
        with self.assertRaises(EnvelopeParseError) as ctx:
            parse_tool_call_envelope_v2(payload)
        self.assertEqual(ctx.exception.code, expected_code)
        return ctx.exception

    def test_non_dict_payload_returns_invalid_envelope_shape(self) -> None:
        self._assert_raises_with_code(
            "not a dict", expected_code=ERROR_INVALID_ENVELOPE_SHAPE
        )

    def test_missing_contract_version_returns_invalid_envelope_version(self) -> None:
        payload = _valid_call_envelope_payload()
        del payload["contract_version"]
        self._assert_raises_with_code(
            payload, expected_code=ERROR_INVALID_ENVELOPE_VERSION
        )

    def test_wrong_contract_version_returns_invalid_envelope_version(self) -> None:
        payload = _valid_call_envelope_payload()
        payload["contract_version"] = "v1"
        self._assert_raises_with_code(
            payload, expected_code=ERROR_INVALID_ENVELOPE_VERSION
        )

    def test_calls_not_a_list_returns_invalid_envelope_shape(self) -> None:
        payload = _valid_call_envelope_payload()
        payload["calls"] = {"call_a": {}}  # dict, not list
        self._assert_raises_with_code(
            payload, expected_code=ERROR_INVALID_ENVELOPE_SHAPE
        )

    def test_call_entry_not_a_dict_returns_invalid_call_shape(self) -> None:
        payload = _valid_call_envelope_payload()
        payload["calls"] = ["just a string"]
        self._assert_raises_with_code(payload, expected_code=ERROR_INVALID_CALL_SHAPE)

    def test_missing_call_id_returns_invalid_call_shape(self) -> None:
        payload = _valid_call_envelope_payload()
        payload["calls"][0]["id"] = ""
        self._assert_raises_with_code(payload, expected_code=ERROR_INVALID_CALL_SHAPE)

    def test_missing_call_name_returns_invalid_call_shape(self) -> None:
        payload = _valid_call_envelope_payload()
        del payload["calls"][0]["name"]
        self._assert_raises_with_code(payload, expected_code=ERROR_INVALID_CALL_SHAPE)

    def test_non_object_arguments_returns_invalid_call_shape(self) -> None:
        # Contract sec 6.2 rule 2: arguments MUST be an object.
        payload = _valid_call_envelope_payload()
        payload["calls"][0]["arguments"] = "query string"
        err = self._assert_raises_with_code(
            payload, expected_code=ERROR_INVALID_CALL_SHAPE
        )
        self.assertEqual(err.details.get("field"), "arguments")
        self.assertEqual(err.details.get("type"), "str")

    def test_non_list_depends_on_returns_invalid_call_shape(self) -> None:
        payload = _valid_call_envelope_payload()
        payload["calls"][1]["depends_on"] = "call_a"  # string, not list
        self._assert_raises_with_code(payload, expected_code=ERROR_INVALID_CALL_SHAPE)

    def test_non_string_depends_on_entry_returns_invalid_call_shape(self) -> None:
        payload = _valid_call_envelope_payload()
        payload["calls"][1]["depends_on"] = [42]
        self._assert_raises_with_code(payload, expected_code=ERROR_INVALID_CALL_SHAPE)

    def test_duplicate_call_id_returns_duplicate_call_id_code(self) -> None:
        payload = _valid_call_envelope_payload()
        payload["calls"][1]["id"] = payload["calls"][0]["id"]
        err = self._assert_raises_with_code(
            payload, expected_code=ERROR_DUPLICATE_CALL_ID
        )
        self.assertEqual(
            err.details.get("duplicate_call_id"), payload["calls"][0]["id"]
        )
        self.assertEqual(err.details.get("index"), 1)


class ToolResultEnvelopeV2RoundTripTests(unittest.TestCase):
    def test_parse_full_payload_returns_typed_envelope(self) -> None:
        envelope = parse_tool_result_envelope_v2(_valid_result_envelope_payload())
        self.assertIsInstance(envelope, ToolResultEnvelopeV2)
        self.assertEqual(envelope.contract_version, CONTRACT_VERSION_V2)
        self.assertEqual(len(envelope.results), 1)
        only_result = envelope.results[0]
        self.assertIsInstance(only_result, ToolResultV2)
        self.assertEqual(only_result.id, "call_a")
        self.assertEqual(only_result.name, "tavily.web.search")
        self.assertTrue(only_result.ok)
        self.assertEqual(only_result.status, "success")
        self.assertEqual(
            only_result.data, {"results": [{"url": "https://example.com"}]}
        )
        self.assertTrue(only_result.verified)
        self.assertEqual(only_result.duration_ms, 182)

    def test_round_trip_serialize_then_parse_is_idempotent(self) -> None:
        envelope = parse_tool_result_envelope_v2(_valid_result_envelope_payload())
        round_tripped = parse_tool_result_envelope_v2(
            serialize_tool_result_envelope_v2(envelope)
        )
        self.assertEqual(round_tripped, envelope)

    def test_result_defaults_applied_when_optional_keys_omitted(self) -> None:
        payload = {
            "contract_version": CONTRACT_VERSION_V2,
            "request_id": "req_d",
            "session_id": "sess_d",
            "turn_id": "turn_d",
            "results": [
                {
                    "id": "call_d",
                    "name": "time.now",
                    "ok": True,
                    # status, error_code, error_message, data, verified, duration_ms omitted
                },
            ],
        }
        envelope = parse_tool_result_envelope_v2(payload)
        only_result = envelope.results[0]
        self.assertEqual(only_result.status, DEFAULT_RESULT_STATUS)
        self.assertEqual(only_result.error_code, "")
        self.assertEqual(only_result.error_message, "")
        self.assertEqual(only_result.data, {})
        self.assertFalse(only_result.verified)
        self.assertEqual(only_result.duration_ms, 0)


class ToolResultEnvelopeV2NegativePathTests(unittest.TestCase):
    def _assert_raises_with_code(
        self,
        payload: object,
        *,
        expected_code: str,
    ) -> EnvelopeParseError:
        with self.assertRaises(EnvelopeParseError) as ctx:
            parse_tool_result_envelope_v2(payload)
        self.assertEqual(ctx.exception.code, expected_code)
        return ctx.exception

    def test_non_dict_payload_returns_invalid_envelope_shape(self) -> None:
        self._assert_raises_with_code(
            ["list", "instead", "of", "dict"],
            expected_code=ERROR_INVALID_ENVELOPE_SHAPE,
        )

    def test_wrong_contract_version_returns_invalid_envelope_version_result(
        self,
    ) -> None:
        payload = _valid_result_envelope_payload()
        payload["contract_version"] = "v1"
        self._assert_raises_with_code(
            payload, expected_code=ERROR_INVALID_ENVELOPE_VERSION
        )

    def test_results_not_a_list_returns_invalid_envelope_shape(self) -> None:
        payload = _valid_result_envelope_payload()
        payload["results"] = "not a list"
        self._assert_raises_with_code(
            payload, expected_code=ERROR_INVALID_ENVELOPE_SHAPE
        )

    def test_missing_result_id_returns_invalid_result_shape(self) -> None:
        payload = _valid_result_envelope_payload()
        payload["results"][0]["id"] = ""
        self._assert_raises_with_code(payload, expected_code=ERROR_INVALID_RESULT_SHAPE)

    def test_missing_result_name_returns_invalid_result_shape(self) -> None:
        payload = _valid_result_envelope_payload()
        del payload["results"][0]["name"]
        self._assert_raises_with_code(payload, expected_code=ERROR_INVALID_RESULT_SHAPE)

    def test_non_bool_ok_returns_invalid_result_shape(self) -> None:
        payload = _valid_result_envelope_payload()
        payload["results"][0]["ok"] = "yes"
        self._assert_raises_with_code(payload, expected_code=ERROR_INVALID_RESULT_SHAPE)

    def test_non_object_data_returns_invalid_result_shape(self) -> None:
        payload = _valid_result_envelope_payload()
        payload["results"][0]["data"] = ["not a dict"]
        self._assert_raises_with_code(payload, expected_code=ERROR_INVALID_RESULT_SHAPE)
