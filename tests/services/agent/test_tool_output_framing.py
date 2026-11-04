from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from openminion.base.config.io import load_config
from openminion.services.agent.service import AgentService, _tool_result_payload_entry


def _item(*, content: str, with_artifact: bool) -> SimpleNamespace:
    data = (
        {"artifact_refs": [{"ref": "artifact:stdout", "role": "stdout"}]}
        if with_artifact
        else {}
    )
    return SimpleNamespace(
        call_id="call-1",
        tool_name="exec.run",
        ok=True,
        verified=True,
        content=content,
        error="",
        data=data,
        source="exec",
    )


def _parsed_config(tmp_path, value=None):
    runtime = {} if value is None else {"agent_loop_tool_result_max_chars": value}
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"runtime": runtime}), encoding="utf-8")
    return load_config(config_path=str(path), home_root=tmp_path)


def _service(config):
    service = object.__new__(AgentService)
    service._config = config
    return service


@pytest.mark.parametrize(
    ("configured", "expected", "payload_chars"),
    [(None, 4000, 4096), (250, 256, 4096), (16384, 16384, 9000)],
)
def test_batch_metadata_uses_the_parsed_configuration(
    tmp_path, configured, expected, payload_chars
) -> None:
    config = _parsed_config(tmp_path, configured)
    assert config.runtime.agent_loop_tool_result_max_chars == expected

    content = "x" * payload_chars
    batch = SimpleNamespace(
        results=[_item(content=content, with_artifact=True)], all_verified=True
    )

    metadata = AgentService._tool_batch_metadata(
        _service(config), batch=batch, tool_calls_count=1
    )
    entry = json.loads(metadata["tool_results"])[0]

    if expected < len(content):
        assert entry["content"].startswith("[tool output omitted")
        assert entry["tool_output_frame"]["threshold_chars"] == expected
    else:
        assert entry["content"] == content
        assert "tool_output_frame" not in entry


def test_lowered_setting_elides_output_that_previously_passed_through() -> None:
    content = "x" * 512
    item = _item(content=content, with_artifact=True)

    default_entry = _tool_result_payload_entry(item)
    lowered_entry = _tool_result_payload_entry(item, threshold_chars=256)

    assert default_entry["content"] == content
    assert "tool_output_frame" not in default_entry
    assert lowered_entry["content"].startswith("[tool output omitted")
    assert lowered_entry["tool_output_frame"]["threshold_chars"] == 256
    assert lowered_entry["tool_output_frame"]["original_chars"] == 512


def test_output_without_artifact_refs_is_never_elided() -> None:
    item = _item(content="x" * 4096, with_artifact=False)

    entry = _tool_result_payload_entry(item, threshold_chars=256)

    assert entry["content"] == "x" * 4096
    assert "tool_output_frame" not in entry


def test_output_within_threshold_is_untouched() -> None:
    item = _item(content="short output", with_artifact=True)

    entry = _tool_result_payload_entry(item, threshold_chars=256)

    assert entry["content"] == "short output"
    assert "tool_output_frame" not in entry
