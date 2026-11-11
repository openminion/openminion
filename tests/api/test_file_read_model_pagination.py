"""Check native file pagination through serialized SDK provider requests."""

import json

import httpx
import pytest
from openminion import Agent, APIRuntime
from openminion.base.config import OpenMinionConfig, save_config
from pydantic import BaseModel
from tests._csc_fixtures import _csc_install_default_agent


class Answer(BaseModel):
    answer: str


@pytest.mark.parametrize(
    ("length", "max_chars", "offset", "page_lengths"),
    [
        (1199, None, 0, [1199]),
        (1200, None, 0, [1200]),
        (1201, None, 0, [1200, 1]),
        (1281, 1250, 0, [1200, 81]),
        (1281, 1000, 0, [1000, 281]),
        (3701, 1250, 41, [1200, 1200, 1200, 60]),
    ],
)
def test_native_file_read_reconstructs_text_from_provider_pages(
    tmp_path, monkeypatch, length, max_chars, offset, page_lengths
):
    source = "".join(f"{i:04d}é😀e\u0301\n" for i in range(500))[:length]
    path = tmp_path / "fixture.txt"
    path.write_bytes(source.replace("\n", "\r\n").encode("utf-8"))
    arguments = {"path": str(path), "offset": offset}
    if max_chars is not None:
        arguments["max_chars"] = max_chars

    pages = []
    requested_offsets = []

    def handle(_transport, request):
        assert request.url.host == "sdk-file-probe.invalid"
        payload = json.loads(request.read())
        names = {tool["function"]["name"] for tool in payload.get("tools", [])}
        message = {"role": "assistant", "content": '{"answer":"fixture"}'}
        finish = "stop"
        if "file_read" in names:
            if requested_offsets and len(pages) < len(requested_offsets):
                last_call_id = f"read-{len(requested_offsets)}"
                last = next(
                    item
                    for item in payload["messages"]
                    if item["role"] == "tool" and item["tool_call_id"] == last_call_id
                )
                pages.append(json.loads(last["content"])["outputs"])
            if not pages or pages[-1]["truncated"]:
                if pages:
                    arguments["offset"] = pages[-1]["next_offset"]
                assert len(requested_offsets) <= len(page_lengths)
                requested_offsets.append(arguments["offset"])
                message = {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": f"read-{len(requested_offsets)}",
                            "type": "function",
                            "function": {
                                "name": "file_read",
                                "arguments": json.dumps(arguments),
                            },
                        }
                    ],
                }
                finish = "tool_calls"
        return httpx.Response(
            200,
            json={
                "id": "file-probe",
                "object": "chat.completion",
                "model": "local",
                "choices": [{"index": 0, "message": message, "finish_reason": finish}],
            },
        )

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", handle)
    monkeypatch.setenv("SDK_FILE_PROBE_KEY", "local-fixture")
    config = OpenMinionConfig()
    _csc_install_default_agent(config, provider="openai")
    config.providers.openai.api_key_env = "SDK_FILE_PROBE_KEY"
    config.providers.openai.base_url = "https://sdk-file-probe.invalid/v1"
    config.runtime.process_mode = "single-process"
    config.runtime.daemon_auto_start = False
    config.storage.path = str(tmp_path / "state.db")
    config_path = tmp_path / "agents.json"
    save_config(config, str(config_path))
    runtime = APIRuntime.from_config_path(
        str(config_path), home_root=tmp_path, data_root=tmp_path / "data"
    )
    try:
        Agent(runtime=runtime, tools=["file.read"], output_type=Answer).run(
            "Read all of fixture.txt using file.read and return JSON."
        )
    finally:
        runtime.close()

    expected_offsets = [
        offset + sum(page_lengths[:i]) for i in range(len(page_lengths))
    ]
    assert requested_offsets == expected_offsets
    assert "".join(page["content"] for page in pages) == source[offset:]
    assert [page["returned_length"] for page in pages] == page_lengths
    for index, page in enumerate(pages):
        assert page["total_length"] == length
        assert page["returned_length"] == len(page["content"])
        partial = index + 1 < len(page_lengths)
        assert page["truncated"] is partial
        if partial:
            assert page["next_offset"] == expected_offsets[index + 1]
            assert "hint" in page
        else:
            assert "next_offset" not in page
            assert "hint" not in page
