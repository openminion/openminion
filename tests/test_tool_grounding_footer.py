from __future__ import annotations


from openminion.modules.tool.base import ToolExecutionContext
from openminion.modules.tool.registry import ToolRegistry
from openminion.modules.tool.runtime.registry_toolspec import execute_tool_spec_call
from openminion.modules.tool.runtime.grounding_footer import (
    has_source_footer,
    with_source_footer,
)


class TestWithSourceFooter:
    def test_appends_footer_to_body(self) -> None:
        assert (
            with_source_footer("Body line 1\nBody line 2", "openmeteo")
            == "Body line 1\nBody line 2\nsource=openmeteo"
        )

    def test_idempotent_when_footer_already_present(self) -> None:
        body = "Web search result\nsource=tavily"
        assert with_source_footer(body, "tavily") == body

    def test_handles_empty_content(self) -> None:
        assert with_source_footer("", "time_module") == "source=time_module"

    def test_no_op_for_empty_provider(self) -> None:
        assert with_source_footer("hello", "") == "hello"
        assert with_source_footer("hello", "   ") == "hello"

    def test_normalizes_provider_whitespace_and_case(self) -> None:
        assert with_source_footer("body", "  Tavily  ") == "body\nsource=tavily"

    def test_handles_trailing_newline(self) -> None:
        assert (
            with_source_footer("content\n", "core-http") == "content\nsource=core-http"
        )

    def test_idempotent_on_double_application(self) -> None:
        once = with_source_footer("x", "serper")
        twice = with_source_footer(once, "serper")
        assert once == twice == "x\nsource=serper"


class TestHasSourceFooter:
    def test_detects_footer_anywhere(self) -> None:
        assert has_source_footer("foo\nsource=tavily\nbar")

    def test_returns_false_for_no_footer(self) -> None:
        assert not has_source_footer("no footer here")

    def test_returns_false_for_empty(self) -> None:
        assert not has_source_footer("")


def _ctx(tmp_path) -> ToolExecutionContext:
    return ToolExecutionContext(
        channel="test",
        target="test",
        session_id="s1",
        metadata={"workspace_root": str(tmp_path)},
    )


class TestTimeFooter:
    def test_time_now_emits_source_footer(self, tmp_path) -> None:
        from openminion.tools.time.plugin import register

        reg = ToolRegistry()
        register(reg)
        spec = reg.get("time.now")
        result = execute_tool_spec_call(tool=spec, arguments={}, context=_ctx(tmp_path))
        assert result.ok
        assert result.source == "time_module"
        assert "source=time_module" in result.content


class TestFileFooter:
    def test_file_list_dir_emits_source_footer(self, tmp_path) -> None:
        from openminion.tools.file.plugin import register

        (tmp_path / "child.txt").write_text("hello")
        reg = ToolRegistry()
        register(reg)
        spec = reg.get("file.list_dir")
        result = execute_tool_spec_call(
            tool=spec, arguments={"path": "."}, context=_ctx(tmp_path)
        )
        assert result.ok
        assert result.source == "file_module"
        assert "source=file_module" in result.content

    def test_file_read_emits_source_footer(self, tmp_path) -> None:
        from openminion.tools.file.plugin import register

        target = tmp_path / "x.txt"
        target.write_text("hello")
        reg = ToolRegistry()
        register(reg)
        spec = reg.get("file.read")
        result = execute_tool_spec_call(
            tool=spec, arguments={"path": "x.txt"}, context=_ctx(tmp_path)
        )
        assert result.ok
        assert result.source == "file_module"
        assert "source=file_module" in result.content


class TestLocationFooter:
    def test_location_failure_path_omits_footer(self, tmp_path) -> None:
        # Errors do NOT carry the footer (anti-LLM: footer marks a verified
        # successful tool execution; structural metadata only on ok=True).
        from openminion.tools.location.plugin import register

        reg = ToolRegistry()
        register(reg)
        spec = reg.get("location.get")
        result = execute_tool_spec_call(
            tool=spec,
            arguments={"method": "explicit", "city": ""},
            context=_ctx(tmp_path),
        )
        assert not result.ok
        assert "source=location_module" not in result.content


class TestCentralizedFooterIsIdempotent:
    def test_idempotent_with_pre_existing_internal_footer(
        self, tmp_path, monkeypatch
    ) -> None:
        # Construct a synthetic tool whose handler returns a payload that
        # already has the footer in `content` AND a `source` field. The
        # centralized append must not add a duplicate line.
        from openminion.modules.tool.registry import ToolSpec

        def _handler(args, ctx):  # noqa: ANN001 - internal test handler
            return {
                "ok": True,
                "content": 'Web search for "x" returned 1 result(s).\nsource=tavily',
                "source": "tavily",
            }

        reg = ToolRegistry()
        reg.add(
            ToolSpec(
                name="search.synthetic",
                args_model=dict,
                min_scope="READ_ONLY",
                handler=_handler,
            )
        )
        result = execute_tool_spec_call(
            tool=reg.get("search.synthetic"),
            arguments={},
            context=_ctx(tmp_path),
        )
        assert result.ok
        assert result.content.count("source=tavily") == 1


class TestFooterSkippedOnError:
    def test_no_footer_emitted_for_ok_false(self, tmp_path) -> None:
        from openminion.modules.tool.registry import ToolSpec

        def _handler(args, ctx):  # noqa: ANN001 - internal test handler
            return {
                "ok": False,
                "error": {"code": "BOOM", "message": "nope"},
                "source": "should_not_appear",
            }

        reg = ToolRegistry()
        reg.add(
            ToolSpec(
                name="x.fail",
                args_model=dict,
                min_scope="READ_ONLY",
                handler=_handler,
            )
        )
        result = execute_tool_spec_call(
            tool=reg.get("x.fail"), arguments={}, context=_ctx(tmp_path)
        )
        assert not result.ok
        assert "source=should_not_appear" not in result.content


class TestSourceMappingExtractedFromDict:
    def test_weather_style_mapping_source(self, tmp_path) -> None:
        from openminion.modules.tool.registry import ToolSpec

        def _handler(args, ctx):  # noqa: ANN001 - internal test handler
            return {
                "ok": True,
                "summary": "Weather lookup OK",
                "metrics": {},
                "source": {"provider_id": "openmeteo", "name": "Open-Meteo"},
            }

        reg = ToolRegistry()
        reg.add(
            ToolSpec(
                name="weather.synth",
                args_model=dict,
                min_scope="READ_ONLY",
                handler=_handler,
            )
        )
        result = execute_tool_spec_call(
            tool=reg.get("weather.synth"),
            arguments={},
            context=_ctx(tmp_path),
        )
        assert result.ok
        assert result.source == "openmeteo"
        assert "source=openmeteo" in result.content
