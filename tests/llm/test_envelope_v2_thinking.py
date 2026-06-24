from __future__ import annotations

from openminion.modules.llm.providers.envelope_v2 import (
    CONTRACT_MINOR_VERSION_V2_1,
    CONTRACT_VERSION_V2,
    ThinkingBlockV2,
    ToolCallEnvelopeV2,
    ToolCallV2,
)


class TestThinkingBlockV2:
    def test_defaults(self):
        block = ThinkingBlockV2()
        assert block.type == "thinking"
        assert block.content == ""
        assert block.signature is None
        assert block.redacted is False

    def test_with_content(self):
        block = ThinkingBlockV2(content="Let me reason step by step.")
        assert block.content == "Let me reason step by step."


class TestToolCallEnvelopeV2_Thinking:
    def test_default_thinking_blocks_empty(self):
        env = ToolCallEnvelopeV2(
            request_id="req-1",
            session_id="sess-1",
            turn_id="turn-1",
        )
        assert env.thinking_blocks == []
        # Contract version stays "v2" — the v2.1 minor bump is documented
        # in the v2 contract spec; the envelope itself keeps the v2 tag.
        assert env.contract_version == CONTRACT_VERSION_V2

    def test_thinking_blocks_populated(self):
        env = ToolCallEnvelopeV2(
            request_id="req-1",
            session_id="sess-1",
            turn_id="turn-1",
            calls=[ToolCallV2(id="c1", name="weather")],
            thinking_blocks=[
                ThinkingBlockV2(content="Need to call weather"),
                ThinkingBlockV2(content="Result looks fine"),
            ],
        )
        assert len(env.thinking_blocks) == 2
        assert env.thinking_blocks[0].content == "Need to call weather"


def test_minor_version_constant_exists():

    assert CONTRACT_MINOR_VERSION_V2_1 == "v2.1"


# ITE-07 round-trip tests — without these the parse/serialize pair drops
# thinking_blocks silently, which was the High-severity gap caught in the


class TestParseSerializeRoundTrip:
    def test_envelope_with_thinking_blocks_round_trips(self):
        from openminion.modules.llm.providers.envelope_v2 import (
            parse_tool_call_envelope_v2,
            serialize_tool_call_envelope_v2,
        )

        original = ToolCallEnvelopeV2(
            request_id="req-1",
            session_id="sess-1",
            turn_id="turn-1",
            calls=[ToolCallV2(id="c1", name="weather")],
            thinking_blocks=[
                ThinkingBlockV2(content="Need to call weather"),
                ThinkingBlockV2(
                    content="redacted block",
                    signature="sig-abc",
                    redacted=True,
                ),
            ],
        )
        wire = serialize_tool_call_envelope_v2(original)
        # thinking_blocks must be present on the wire when non-empty.
        assert "thinking_blocks" in wire
        assert len(wire["thinking_blocks"]) == 2
        # signature only emitted when non-None.
        assert "signature" not in wire["thinking_blocks"][0]
        assert wire["thinking_blocks"][1]["signature"] == "sig-abc"

        # Round-trip back through the parser.
        revived = parse_tool_call_envelope_v2(wire)
        assert len(revived.thinking_blocks) == 2
        assert revived.thinking_blocks[0].content == "Need to call weather"
        assert revived.thinking_blocks[1].redacted is True
        assert revived.thinking_blocks[1].signature == "sig-abc"

    def test_v2_0_payload_without_thinking_blocks_still_parses(self):

        from openminion.modules.llm.providers.envelope_v2 import (
            parse_tool_call_envelope_v2,
        )

        legacy_wire = {
            "contract_version": "v2",
            "request_id": "req-1",
            "session_id": "sess-1",
            "turn_id": "turn-1",
            "calls": [
                {
                    "id": "c1",
                    "name": "weather",
                    "arguments": {},
                    "depends_on": [],
                    "execution_hint": "auto",
                    "source": "native",
                }
            ],
        }
        parsed = parse_tool_call_envelope_v2(legacy_wire)
        assert parsed.thinking_blocks == []

    def test_envelope_with_empty_thinking_blocks_omits_field_on_wire(self):

        from openminion.modules.llm.providers.envelope_v2 import (
            serialize_tool_call_envelope_v2,
        )

        env = ToolCallEnvelopeV2(
            request_id="req-1",
            session_id="sess-1",
            turn_id="turn-1",
        )
        wire = serialize_tool_call_envelope_v2(env)
        assert "thinking_blocks" not in wire

    def test_parse_rejects_non_list_thinking_blocks(self):
        from openminion.modules.llm.providers.envelope_v2 import (
            EnvelopeParseError,
            parse_tool_call_envelope_v2,
        )

        bad_wire = {
            "contract_version": "v2",
            "request_id": "r",
            "session_id": "s",
            "turn_id": "t",
            "calls": [],
            "thinking_blocks": "not-a-list",
        }
        try:
            parse_tool_call_envelope_v2(bad_wire)
        except EnvelopeParseError as exc:
            assert (
                "thinking_blocks" in str(exc).lower()
                or exc.details.get("field") == "thinking_blocks"
            )
        else:
            raise AssertionError("expected EnvelopeParseError")

    def test_parse_rejects_non_dict_thinking_block_entry(self):
        from openminion.modules.llm.providers.envelope_v2 import (
            EnvelopeParseError,
            parse_tool_call_envelope_v2,
        )

        bad_wire = {
            "contract_version": "v2",
            "request_id": "r",
            "session_id": "s",
            "turn_id": "t",
            "calls": [],
            "thinking_blocks": ["string-not-dict"],
        }
        try:
            parse_tool_call_envelope_v2(bad_wire)
        except EnvelopeParseError as exc:
            assert exc.details.get("field") == "thinking_blocks"
        else:
            raise AssertionError("expected EnvelopeParseError")
