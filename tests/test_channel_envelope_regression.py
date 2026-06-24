import unittest

from openminion.modules.llm.providers.tool_calling import (
    _CHANNEL_ENVELOPE_RE,
    _CHANNEL_ENVELOPE_MALFORMED_RE,
    _extract_channel_envelope_calls,
    extract_fallback_tool_calls_from_text,
)


class TestChannelEnvelopePositiveCases(unittest.TestCase):
    def test_basic_channel_envelope_parsing(self) -> None:
        text = '<|start|>assistant<|channel|>commentary to=tool.file.list_dir <|constrain|>json<|message|>{"path": "."}<|call|>'
        calls = _extract_channel_envelope_calls(
            text, allowed_tool_names=["file.list_dir"]
        )
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].name, "file.list_dir")
        self.assertEqual(calls[0].arguments.get("path"), ".")

    def test_channel_envelope_with_list_files(self) -> None:
        text = '<|start|>assistant<|channel|>commentary to=tool.file.list_dir <|constrain|>json<|message|>{"path": "/tmp"}<|call|>'
        calls = _extract_channel_envelope_calls(
            text, allowed_tool_names=["file.list_dir"]
        )
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].name, "file.list_dir")
        self.assertEqual(calls[0].arguments.get("path"), "/tmp")

    def test_channel_envelope_with_file_read(self) -> None:
        text = '<|start|>assistant<|channel|>commentary to=tool.file.read <|constrain|>json<|message|>{"file_path": "/workspace/test.txt"}<|call|>'
        calls = _extract_channel_envelope_calls(text, allowed_tool_names=["file.read"])
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].name, "file.read")

    def test_channel_envelope_with_file_find(self) -> None:
        text = '<|start|>assistant<|channel|>commentary to=tool.file.find <|constrain|>json<|message|>{"pattern": "*.py"}<|call|>'
        calls = _extract_channel_envelope_calls(text, allowed_tool_names=["file.find"])
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].name, "file.find")

    def test_channel_envelope_case_insensitive(self) -> None:
        text = '<|start|>assistant<|channel|>commentary to=tool.FILE.LIST_DIR <|constrain|>json<|message|>{"path": "."}<|call|>'
        calls = _extract_channel_envelope_calls(
            text, allowed_tool_names=["file.list_dir"]
        )
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].name, "file.list_dir")

    def test_multiple_channel_envelopes(self) -> None:
        text = (
            '<|start|>assistant<|channel|>commentary to=tool.file.list_dir <|constrain|>json<|message|>{"path": "."}<|call|>\n'
            '<|start|>assistant<|channel|>commentary to=tool.file.read <|constrain|>json<|message|>{"file_path": "test.txt"}<|call|>'
        )
        calls = _extract_channel_envelope_calls(
            text, allowed_tool_names=["file.list_dir", "file.read"]
        )
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0].name, "file.list_dir")
        self.assertEqual(calls[1].name, "file.read")

    def test_fallback_parser_uses_channel_envelope(self) -> None:
        text = '<|start|>assistant<|channel|>commentary to=tool.file.list_dir <|constrain|>json<|message|>{"path": "."}<|call|>'
        calls = extract_fallback_tool_calls_from_text(
            text, allowed_tool_names=["file.list_dir"]
        )
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].name, "file.list_dir")
        self.assertEqual(calls[0].source, "fallback")


class TestChannelEnvelopeAliasResolution(unittest.TestCase):
    def test_list_files_alias_in_envelope(self) -> None:
        text = '<|start|>assistant<|channel|>commentary to=tool.file.list_dir <|constrain|>json<|message|>{"path": "."}<|call|>'
        calls = extract_fallback_tool_calls_from_text(
            text, allowed_tool_names=["file.list_dir"]
        )
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].name, "file.list_dir")

    def test_tool_dot_prefix_handled(self) -> None:
        text = '<|start|>assistant<|channel|>commentary to=tool.file.list_dir <|constrain|>json<|message|>{"path": "."}<|call|>'
        calls = extract_fallback_tool_calls_from_text(
            text, allowed_tool_names=["file.list_dir"]
        )
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].name, "file.list_dir")


class TestChannelEnvelopeNegativeCases(unittest.TestCase):
    def test_missing_json_args_rejected(self) -> None:
        text = "<|start|>assistant<|channel|>commentary to=tool.file.list_dir <|constrain|>json<|message|>not valid json<|call|>"
        calls = _extract_channel_envelope_calls(
            text, allowed_tool_names=["file.list_dir"]
        )
        self.assertEqual(len(calls), 0)

    def test_unallowed_tool_rejected(self) -> None:
        text = '<|start|>assistant<|channel|>commentary to=tool.file.list_dir <|constrain|>json<|message|>{"path": "."}<|call|>'
        calls = _extract_channel_envelope_calls(text, allowed_tool_names=["file.read"])
        self.assertEqual(len(calls), 0)

    def test_malformed_envelope_no_crash(self) -> None:
        text = "<|start|>assistant<|channel|>commentary to=tool."
        calls = _extract_channel_envelope_calls(
            text, allowed_tool_names=["file.list_dir"]
        )
        self.assertEqual(len(calls), 0)

    def test_no_allowed_tools_returns_empty(self) -> None:
        text = '<|start|>assistant<|channel|>commentary to=tool.file.list_dir <|constrain|>json<|message|>{"path": "."}<|call|>'
        calls = _extract_channel_envelope_calls(text, allowed_tool_names=set())
        self.assertEqual(len(calls), 0)

    def test_empty_text_returns_empty(self) -> None:
        calls = _extract_channel_envelope_calls(
            "", allowed_tool_names=["file.list_dir"]
        )
        self.assertEqual(len(calls), 0)

    def test_whitespace_only_returns_empty(self) -> None:
        calls = _extract_channel_envelope_calls(
            "   ", allowed_tool_names=["file.list_dir"]
        )
        self.assertEqual(len(calls), 0)


class TestChannelEnvelopeRegex(unittest.TestCase):
    def test_channel_envelope_regex_matches_expected(self) -> None:
        text = '<|start|>assistant<|channel|>commentary to=tool.file.list_dir <|constrain|>json<|message|>{"path": "."}<|call|>'
        match = _CHANNEL_ENVELOPE_RE.search(text)
        self.assertIsNotNone(match)
        self.assertEqual(match.group("tool_name"), "file.list_dir")
        self.assertEqual(match.group("json_args"), '{"path": "."}')

    def test_malformed_detection_regex(self) -> None:
        text = "<|start|>assistant<|channel|>commentary to=tool."
        match = _CHANNEL_ENVELOPE_MALFORMED_RE.search(text)
        self.assertIsNotNone(match)


class TestFallbackParserIntegration(unittest.TestCase):
    def test_channel_envelope_takes_precedence(self) -> None:
        text = (
            "<|start|>assistant<|channel|>commentary to=tool.file.list_dir "
            '<|constrain|>json<|message|>{"path": "."}<|call|>\n'
            "plain fallback prose that should be ignored once the envelope parses"
        )
        calls = extract_fallback_tool_calls_from_text(
            text, allowed_tool_names=["file.list_dir"]
        )
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].name, "file.list_dir")


class TestGenericEnvelopeTargets(unittest.TestCase):
    def test_browser_run_envelope_rejected(self) -> None:
        from openminion.modules.llm.providers.tool_calling import (
            extract_fallback_tool_calls_from_text_with_metadata,
        )

        text = '<|start|>assistant<|channel|>commentary to=browser.run <|message|>{"url": "https://example.com"}<|call|>'
        calls, metadata = extract_fallback_tool_calls_from_text_with_metadata(
            text, allowed_tool_names=["browser.pinchtab.navigate"]
        )
        self.assertEqual(calls, [])
        self.assertEqual(metadata.get("envelope_target_raw"), "browser.run")
        self.assertEqual(metadata.get("envelope_rejected_reason"), "tool_not_allowed")

    def test_search_web_envelope_rejected(self) -> None:
        from openminion.modules.llm.providers.tool_calling import (
            extract_fallback_tool_calls_from_text_with_metadata,
        )

        text = '<|start|>assistant<|channel|>commentary to=search.web <|message|>{"query": "test"}<|call|>'
        calls, metadata = extract_fallback_tool_calls_from_text_with_metadata(
            text, allowed_tool_names=["web.search"]
        )
        self.assertEqual(calls, [])
        self.assertEqual(metadata.get("envelope_target_raw"), "search.web")
        self.assertEqual(metadata.get("envelope_rejected_reason"), "tool_not_allowed")

    def test_functions_prefix_envelope_parsed(self) -> None:
        from openminion.modules.llm.providers.tool_calling import (
            extract_fallback_tool_calls_from_text_with_metadata,
        )

        text = '<|start|>assistant<|channel|>commentary to=functions.weather <|message|>{"location":"San Francisco"}<|call|>'
        calls, metadata = extract_fallback_tool_calls_from_text_with_metadata(
            text, allowed_tool_names=["weather"]
        )
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].name, "weather")
        self.assertEqual(calls[0].arguments.get("location"), "San Francisco")
        self.assertEqual(metadata.get("envelope_target_raw"), "functions.weather")
        self.assertEqual(metadata.get("envelope_target_normalized"), "weather")

    def test_tool_request_wrapper_is_rejected(self) -> None:
        from openminion.modules.llm.providers.tool_calling import (
            extract_fallback_tool_calls_from_text_with_metadata,
        )

        text = (
            "<|start|>assistant<|channel|>commentary to=tool.request "
            '<|constrain|>json<|message|>{"command":"search","query":"latest iran news","top_k":5}<|call|>'
        )
        calls, metadata = extract_fallback_tool_calls_from_text_with_metadata(
            text, allowed_tool_names=["web.search"]
        )
        self.assertEqual(calls, [])
        self.assertEqual(metadata.get("envelope_target_raw"), "tool.request")
        self.assertEqual(
            metadata.get("envelope_rejected_reason"),
            "unsupported_tool_request_wrapper",
        )

    def test_unknown_target_rejected(self) -> None:
        from openminion.modules.llm.providers.tool_calling import (
            extract_fallback_tool_calls_from_text_with_metadata,
        )

        text = '<|start|>assistant<|channel|>commentary to=unknown.tool <|message|>{"x": 1}<|call|>'
        calls, metadata = extract_fallback_tool_calls_from_text_with_metadata(
            text, allowed_tool_names=["web.search"]
        )
        self.assertEqual(len(calls), 0)
        # unknown.tool is normalized as-is (not in namespace map), then rejected as not allowed
        self.assertIn(
            metadata.get("envelope_rejected_reason"),
            ["unknown_target", "tool_not_allowed"],
        )


class TestNoLeakGuard(unittest.TestCase):
    def test_detect_raw_envelope(self) -> None:
        from openminion.modules.llm.providers.tool_calling import detect_raw_envelope

        text = '<|start|>assistant<|channel|>commentary to=browser.run <|message|>{"x": 1}<|call|>'
        self.assertTrue(detect_raw_envelope(text))

    def test_no_envelope_not_detected(self) -> None:
        from openminion.modules.llm.providers.tool_calling import detect_raw_envelope

        text = "This is normal assistant response text."
        self.assertFalse(detect_raw_envelope(text))

    def test_sanitize_envelope_leak_success(self) -> None:
        from openminion.modules.llm.providers.tool_calling import sanitize_envelope_leak

        text = '<|start|>assistant<|channel|>commentary to=browser.run <|message|>{"url": "x"}<|call|>'
        metadata = {"envelope_target_normalized": "browser.pinchtab.navigate"}
        result = sanitize_envelope_leak(text, metadata=metadata)
        self.assertEqual(result, text)

    def test_sanitize_envelope_leak_blocked(self) -> None:
        from openminion.modules.llm.providers.tool_calling import sanitize_envelope_leak

        text = '<|start|>assistant<|channel|>commentary to=browser.run <|message|>{"x": 1}<|call|>'
        metadata = {
            "envelope_rejected_reason": "unknown_target",
            "envelope_target_raw": "browser.run",
        }
        result = sanitize_envelope_leak(text, metadata=metadata)
        self.assertIn("UNEXECUTABLE_TOOL_ENVELOPE", result)
        self.assertIn("browser.run", result)
        self.assertNotIn("<|start|>", result)

    def test_sanitize_no_metadata(self) -> None:
        from openminion.modules.llm.providers.tool_calling import sanitize_envelope_leak

        text = "<|start|>assistant<|channel|>commentary to=x <|message|>{}<|call|>"
        result = sanitize_envelope_leak(text)
        self.assertIn("UNEXECUTABLE_TOOL_ENVELOPE", result)
