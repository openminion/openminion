"""Regression controls for bounded tool-message truncation facts and neighbors."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from openminion import Agent, APIRuntime, tool
from openminion.base.config import OpenMinionConfig, save_config
from openminion.modules.brain.adapters.artifact.runtime import ArtifactctlAdapter
from openminion.modules.brain.constants import (
    TOOL_MESSAGE_DEPTH_LIMIT,
    TOOL_MESSAGE_SEQUENCE_LIMIT,
    TOOL_MESSAGE_STRING_LIMIT,
)
from openminion.modules.brain.execution.validation import normalize_execution_result
from openminion.modules.brain.loop.tools.messages import action_result_to_tool_message
from openminion.modules.brain.schemas import ActionError, ActionResult, ArtifactRef
from openminion.modules.context.input_boundaries import (
    drain_ledger,
    escape_untrusted_content,
    wrap_input_content,
)
from openminion.modules.llm.providers.anthropic.payloads import _messages_anthropic
from openminion.modules.llm.providers.message_payloads import _messages_openai_like
from openminion.modules.llm.schemas import LLMRequest, Message, ToolCall
from openminion.modules.tool.contracts.schemas import TOOL_ERROR_CONFIRM_REQUIRED
from openminion.modules.tool.plugin_contract.sinks import MemoryArtifactSink
from openminion.modules.tool.runtime import RuntimeContext
from openminion.modules.tool.runtime.policy import Policy
from openminion.tools.exec.results import _artifactize_output
from openminion.tools.fetch.plugin import _format_get_response
from tests._csc_fixtures import _csc_install_default_agent
from tests.artifact.utils import artifact_ctl


def _serialize_openai_tool_body(call_id: str, tool_name: str, tool_msg: Message) -> str:
    request = LLMRequest(
        messages=[
            Message(
                role="assistant",
                tool_calls=[ToolCall(id=call_id, name=tool_name, arguments={})],
            ),
            tool_msg,
        ]
    )
    rendered = _messages_openai_like(request, include_fallback_instruction=False)
    assert [item["role"] for item in rendered] == ["assistant", "tool"]
    assert rendered[1]["tool_call_id"] == call_id
    content = rendered[1]["content"]
    assert isinstance(content, str)
    return content


def _expected_fenced_bytes(body: str) -> int:
    escaped = escape_untrusted_content(wrap_input_content("tool_output", body))
    return len(escaped.content.encode("utf-8"))


def _runtime_ctx(
    tmp_path: Path, name: str, *, artifactctl: Any | None = None
) -> RuntimeContext:
    run_root = tmp_path / name
    run_root.mkdir(parents=True, exist_ok=True)
    ctx = RuntimeContext(
        policy=Policy(raw={}),
        workspace=run_root,
        run_root=run_root,
        scope="READ_ONLY",
        confirm=False,
    )
    if artifactctl is not None:
        ctx.artifactctl = artifactctl
    return ctx


def test_large_stdout_records_exact_truncation_facts_and_boundary_event() -> None:
    drain_ledger()
    stdout = "".join(f"{line:05d}:{'x' * 54}\n" for line in range(6000))[:359_999]
    assert len(stdout) == 359_999
    result = ActionResult(
        command_id="cmd-stdout",
        status="success",
        summary="command completed",
        outputs={"stdout": stdout},
    )

    message = action_result_to_tool_message("call-stdout", "exec.run", result)
    events = drain_ledger()

    expected_payload = {
        "status": "success",
        "summary": "command completed",
        "outputs": {
            "stdout": f"{stdout[:TOOL_MESSAGE_STRING_LIMIT]}...[truncated]",
        },
        "truncation": {
            "truncated": True,
            "field_count": 1,
            "fields": [
                {
                    "path": "outputs.stdout",
                    "kind": "string",
                    "kept": TOOL_MESSAGE_STRING_LIMIT,
                    "total": 359_999,
                }
            ],
        },
    }
    expected_body = json.dumps(expected_payload, ensure_ascii=False)
    assert message.content == expected_body
    assert _serialize_openai_tool_body("call-stdout", "exec.run", message) == (
        expected_body
    )
    _, anthropic_messages = _messages_anthropic(
        LLMRequest(
            messages=[
                Message(
                    role="assistant",
                    tool_calls=[
                        ToolCall(id="call-stdout", name="exec.run", arguments={})
                    ],
                ),
                message,
            ]
        ),
        include_fallback_instruction=False,
    )
    assert anthropic_messages[1]["content"][0]["content"] == expected_body
    assert message.tool_output == expected_payload["outputs"]
    assert message.tool_error is None
    assert message.tool_status == "success"

    assert len(events) == 1
    event = events[0]
    assert event.source == "tool_output"
    assert event.escape_policy == "fence_block"
    assert (
        event.seam_id
        == "modules.brain.loop.tools.messages.action_result_to_tool_message"
    )
    assert event.provenance_ref == "call-stdout"
    assert event.content_size_bytes == _expected_fenced_bytes(expected_body)


def test_recursive_sequence_mapping_depth_and_field_cap_bounds() -> None:
    deep: Any = {"leaf_a": 1, "leaf_b": 2, "leaf_c": 3}
    for level in range(TOOL_MESSAGE_DEPTH_LIMIT, 0, -1):
        deep = {f"d{level}": deep}

    many_keys = {f"k{idx:02d}": idx for idx in range(20)}
    result = ActionResult(
        command_id="cmd-bounds",
        status="success",
        summary="s" * 1500,
        outputs={
            "items": list(range(5000)),
            "mapping": many_keys,
            **deep,
            "rows": [{"body": "b" * 2000} for _ in range(16)],
        },
    )

    drain_ledger()
    message = action_result_to_tool_message("call-bounds", "custom.tool", result)
    events = drain_ledger()
    payload = json.loads(message.content)

    assert len(events) == 1
    assert payload["outputs"]["items"][-1] == "...[4984 more item(s)]"
    assert payload["outputs"]["mapping"]["__truncated__"] == "4 more key(s)"
    cursor = payload["outputs"]
    for level in range(1, TOOL_MESSAGE_DEPTH_LIMIT + 1):
        cursor = cursor[f"d{level}"]
    assert cursor == "[truncated]"

    truncation = payload["truncation"]
    assert truncation["truncated"] is True
    assert truncation["field_count"] == 20
    assert truncation["omitted_fields"] == 4
    assert len(truncation["fields"]) == TOOL_MESSAGE_SEQUENCE_LIMIT
    assert truncation["fields"][:4] == [
        {"path": "summary", "kind": "string", "kept": 1200, "total": 1500},
        {"path": "outputs.items", "kind": "sequence", "kept": 16, "total": 5000},
        {"path": "outputs.mapping", "kind": "mapping", "kept": 16, "total": 20},
        {
            "path": "outputs."
            + ".".join(f"d{level}" for level in range(1, TOOL_MESSAGE_DEPTH_LIMIT + 1)),
            "kind": "depth",
            "kept": 0,
            "total": 3,
        },
    ]
    assert "artifact_refs" not in truncation
    assert "path" not in truncation


def test_producer_grounded_artifact_refs_and_reader_roundtrip(tmp_path: Path) -> None:
    with artifact_ctl(tmp_path) as ctl:
        adapter = ArtifactctlAdapter(ctl)
        full_stdout = "exec-line\n" * 260
        created_raw = adapter.execute(
            command={
                "tool_name": "create_artifact",
                "args": {
                    "content": full_stdout,
                    "mime": "text/plain",
                    "label": "stdout.txt",
                },
            },
            session_id="s-prod",
            trace_id="t-prod",
        )
        normalized_created, _ = normalize_execution_result(
            command_id="cmd-create",
            raw=created_raw,
            provider="artifact",
            tool_name="create_artifact",
        )
        created_ref = normalized_created.artifact_refs[0].ref

        exec_ctx = _runtime_ctx(tmp_path, "exec-cas", artifactctl=ctl)
        stdout_artifact = _artifactize_output(
            exec_ctx,
            session_id="s-prod",
            stream="stdout",
            payload=full_stdout.encode("utf-8"),
        )
        stderr_text = "stderr-detail\n" * 180
        stderr_artifact = _artifactize_output(
            exec_ctx,
            session_id="s-prod",
            stream="stderr",
            payload=stderr_text.encode("utf-8"),
        )
        assert stdout_artifact["ref"] == created_ref

        fetch_body = "fetched-body-line\n" * 200
        fetch_payload = _format_get_response(
            request={"url": "https://example.test/doc", "max_response_chars": 1200},
            result={
                "raw_body": fetch_body.encode("utf-8"),
                "extracted_text": fetch_body,
                "content_type": "text/plain",
                "final_url": "https://example.test/doc",
                "status_code": 200,
                "warnings": [],
            },
            backend_name="http",
            ctx=_runtime_ctx(tmp_path, "fetch-cas", artifactctl=ctl),
        )

        opaque_ingested = ctl.ingest_bytes(
            b"opaque-session-bytes",
            mime="text/plain",
            session_id="s-prod",
            opaque=True,
        )
        local_exec_artifact = _artifactize_output(
            _runtime_ctx(tmp_path, "exec-local", artifactctl=None),
            session_id="s-prod",
            stream="stdout",
            payload=b"local-run-root-only",
        )
        sink_ref = MemoryArtifactSink().put_bytes(
            name="mem.txt", content=b"mem", kind="file"
        )

        combined = ActionResult(
            command_id="cmd-cas",
            status="success",
            summary="captured",
            artifact_refs=[
                *normalized_created.artifact_refs,
                ArtifactRef(ref=opaque_ingested.ref, meta={"opaque": True}),
                ArtifactRef(ref=local_exec_artifact["ref"]),
                ArtifactRef(ref=sink_ref.ref),
                ArtifactRef(ref=ctl.get(created_ref).sha256),
            ],
            outputs={
                **{f"pad_{idx:02d}": idx for idx in range(18)},
                "stdout": full_stdout,
                "stdout_artifact": stdout_artifact,
                "stderr_artifact": stderr_artifact,
                "artifacts": {
                    **fetch_payload["artifacts"],
                    "local_only": local_exec_artifact["ref"],
                },
                "evidence_refs": [
                    stderr_artifact["ref"],
                    ctl.get(created_ref).sha256,
                    created_ref.upper(),
                ],
                "artifact_ref": {"canonical_ref": created_ref},
            },
        )
        cas_msg = action_result_to_tool_message("call-cas", "exec.run", combined)
        cas_truncation = json.loads(cas_msg.content)["truncation"]
        expected_refs = [
            created_ref,
            stderr_artifact["ref"],
            fetch_payload["artifacts"]["raw_body"],
            fetch_payload["artifacts"]["metadata_json"],
        ]
        assert cas_truncation["artifact_refs"] == expected_refs
        assert "path" not in cas_truncation

        for ref in cas_truncation["artifact_refs"]:
            read_back = adapter.execute(
                command={"tool_name": "read_artifact", "args": {"id": ref}},
                session_id="s-prod",
                trace_id="t-prod",
            )
            assert read_back["status"] == "success"
        assert ctl.read_bytes(created_ref).decode("utf-8") == full_stdout
        assert ctl.read_bytes(stderr_artifact["ref"]).decode("utf-8") == stderr_text

        many_ingested = [
            ctl.ingest_bytes(f"chunk-{idx}".encode("utf-8"), mime="text/plain").ref
            for idx in range(20)
        ]
        capped_msg = action_result_to_tool_message(
            "call-cap",
            "security.report",
            ActionResult(
                command_id="cmd-cap",
                status="success",
                summary="capped",
                outputs={
                    "body": "w" * 2400,
                    "artifact_ref": many_ingested[0],
                    "artifact_refs": [many_ingested[0], *many_ingested],
                },
            ),
        )
        assert (
            json.loads(capped_msg.content)["truncation"]["artifact_refs"]
            == many_ingested[:TOOL_MESSAGE_SEQUENCE_LIMIT]
        )

        file_backed = ActionResult(
            command_id="cmd-file",
            status="success",
            summary="read",
            outputs={
                "ok": True,
                "path": "/workspace/src/main.py",
                "content": "z" * 4000,
                "truncated": False,
                "total_length": 4000,
                "returned_length": 4000,
                "source": "file_module",
            },
        )
        file_msg = action_result_to_tool_message("call-file", "file.read", file_backed)
        file_truncation = json.loads(file_msg.content)["truncation"]
        assert file_truncation["path"] == "/workspace/src/main.py"
        assert "artifact_refs" not in file_truncation

        unsupported = ActionResult(
            command_id="cmd-unsupported",
            status="success",
            summary="ran",
            artifact_refs=[
                ArtifactRef(ref=local_exec_artifact["ref"]),
                ArtifactRef(ref="artifact://tool/s1/t1/exec.run/c1"),
                ArtifactRef(ref=opaque_ingested.ref, meta={"opaque": True}),
            ],
            outputs={
                "path": "/etc/hosts",
                "stdout": "q" * 2500,
                "stdout_artifact": local_exec_artifact,
                "stderr_artifact": {
                    "ref": created_ref,
                    "meta": {"opaque": True},
                },
                "artifacts": {"speculative": {"canonical_ref": created_ref}},
            },
        )
        unsupported_msg = action_result_to_tool_message(
            "call-unsupported", "exec.run", unsupported
        )
        unsupported_truncation = json.loads(unsupported_msg.content)["truncation"]
        assert "artifact_refs" not in unsupported_truncation
        assert "path" not in unsupported_truncation


def test_redaction_is_preserved_without_leaking_secrets_in_truncation_facts() -> None:
    secret_token = "sk-liveSecretKeyValue1234567890"
    redacted_prefix = "order A-104 api_key=[REDACTED] "
    long_redacted = redacted_prefix + ("r" * 2000)
    result = ActionResult(
        command_id="cmd-redact",
        status="success",
        summary=long_redacted,
        outputs={
            f"Bearer {secret_token}": "v" * 1800,
            "body": long_redacted,
        },
    )

    drain_ledger()
    message = action_result_to_tool_message("call-redact", "custom.lookup", result)
    events = drain_ledger()
    payload = json.loads(message.content)

    assert secret_token not in payload["summary"]
    assert secret_token not in json.dumps(payload["truncation"])
    assert "api_key=[REDACTED]" in payload["summary"]
    assert payload["truncation"]["fields"] == [
        {
            "path": "summary",
            "kind": "string",
            "kept": 1200,
            "total": len(long_redacted),
        },
        {
            "path": "outputs.Bearer [REDACTED]",
            "kind": "string",
            "kept": 1200,
            "total": 1800,
        },
        {
            "path": "outputs.body",
            "kind": "string",
            "kept": 1200,
            "total": len(long_redacted),
        },
    ]
    assert len(events) == 1
    assert events[0].content_size_bytes < 5000


@pytest.mark.parametrize("text_len", [0, 11, TOOL_MESSAGE_STRING_LIMIT])
def test_untruncated_small_and_boundary_payloads_remain_unchanged(
    tmp_path: Path, text_len: int
) -> None:
    nested_at_boundary: Any = {"leaf": "ok"}
    for level in range(TOOL_MESSAGE_DEPTH_LIMIT - 1, 0, -1):
        nested_at_boundary = {f"d{level}": nested_at_boundary}
    content = "a" * text_len
    outputs = {
        "content": content,
        "items": list(range(TOOL_MESSAGE_SEQUENCE_LIMIT)),
        **nested_at_boundary,
    }
    with artifact_ctl(tmp_path) as ctl:
        ingested = ctl.ingest_bytes(b"untruncated-neighbor", mime="text/plain")
        result = ActionResult(
            command_id="cmd-small",
            status="success",
            summary=content,
            outputs=outputs,
            artifact_refs=[ArtifactRef(ref=ingested.ref)],
        )

    drain_ledger()
    message = action_result_to_tool_message("call-small", "file.read", result)
    events = drain_ledger()

    expected_payload = {
        "status": "success",
        "summary": content,
        "outputs": outputs,
    }
    expected_body = json.dumps(expected_payload, ensure_ascii=False)
    assert message.content == expected_body
    assert _serialize_openai_tool_body("call-small", "file.read", message) == (
        expected_body
    )
    assert message.tool_output == outputs
    assert message.tool_error is None
    assert message.tool_status == "success"
    assert message.meta == {"tool_call_id": "call-small"}
    assert len(events) == 1
    assert events[0].content_size_bytes == _expected_fenced_bytes(expected_body)


def test_untruncated_error_and_approval_neighbors_preserve_exact_contracts() -> None:
    retry_result = ActionResult(
        command_id="cmd-retry",
        status="failed",
        summary="rate limited",
        error=ActionError(
            code="RATE_LIMITED",
            message="provider rate limited the call",
            details={"retry_after_ms": 1500},
        ),
    )
    retry_msg = action_result_to_tool_message("call-retry", "web.fetch", retry_result)
    expected_retry = {
        "status": "failed",
        "summary": "rate limited",
        "error": {
            "code": "RATE_LIMITED",
            "message": "provider rate limited the call",
            "details": {"retry_after_ms": 1500},
        },
    }
    assert retry_msg.content == json.dumps(expected_retry, ensure_ascii=False)
    assert retry_msg.tool_status == "error"
    assert retry_msg.tool_output is None
    assert retry_msg.tool_error == expected_retry["error"]

    approval_result = ActionResult(
        command_id="cmd-confirm",
        status="needs_user",
        summary="confirmation required",
        error=ActionError(
            code=TOOL_ERROR_CONFIRM_REQUIRED,
            message="Operator confirmation required.",
            details={
                "approval_id": "apr-42",
                "preview": {"command": "git push"},
                "choices": ["allow_once", "deny"],
            },
        ),
    )
    approval_msg = action_result_to_tool_message(
        "call-confirm", "git.push", approval_result
    )
    expected_approval = {
        "status": "needs_user",
        "summary": "confirmation required",
        "error": {
            "code": TOOL_ERROR_CONFIRM_REQUIRED,
            "message": "Operator confirmation required.",
            "details": {
                "approval_id": "apr-42",
                "preview": {"command": "git push"},
                "choices": ["allow_once", "deny"],
            },
        },
    }
    assert approval_msg.content == json.dumps(expected_approval, ensure_ascii=False)
    assert approval_msg.tool_status == "blocked"
    assert approval_msg.tool_output is None
    assert approval_msg.tool_error == expected_approval["error"]


def test_browser_screenshot_path_neighbor_preserves_vision_content_parts(
    tmp_path: Path,
) -> None:
    screenshot = tmp_path / "shot.png"
    screenshot.write_bytes(b"png")
    path_result = ActionResult(
        command_id="cmd-shot-path",
        status="success",
        summary="screenshot captured",
        outputs={"artifact": {"kind": "screenshot", "path": str(screenshot)}},
    )
    path_msg = action_result_to_tool_message("call-shot-1", "browser", path_result)
    expected_path_body = json.dumps(
        {
            "status": "success",
            "summary": "screenshot captured",
            "outputs": {"artifact": {"kind": "screenshot", "path": str(screenshot)}},
        },
        ensure_ascii=False,
    )
    assert path_msg.content == expected_path_body
    assert [part.type for part in path_msg.content_parts] == ["text", "image"]
    assert path_msg.content_parts[0].text == expected_path_body
    assert path_msg.content_parts[1].path == str(screenshot)
    assert path_msg.meta == {
        "tool_call_id": "call-shot-1",
        "auto_vision_artifact": True,
    }


def test_truncated_error_approval_and_base64_screenshot_record_truncation_facts() -> (
    None
):
    long_error = ActionResult(
        command_id="cmd-long-err",
        status="failed",
        summary="failed",
        error=ActionError(
            code="EXEC_ERROR",
            message="e" * 2500,
            details={"exit_code": 2},
        ),
    )
    long_error_msg = action_result_to_tool_message(
        "call-long-err", "exec.run", long_error
    )
    long_error_payload = json.loads(long_error_msg.content)
    assert long_error_msg.tool_status == "error"
    assert long_error_msg.tool_error == long_error_payload["error"]
    assert long_error_payload["error"] == {
        "code": "EXEC_ERROR",
        "message": f"{'e' * TOOL_MESSAGE_STRING_LIMIT}...[truncated]",
        "details": {"exit_code": 2},
    }
    assert long_error_payload["truncation"] == {
        "truncated": True,
        "field_count": 1,
        "fields": [
            {
                "path": "error.message",
                "kind": "string",
                "kept": TOOL_MESSAGE_STRING_LIMIT,
                "total": 2500,
            }
        ],
    }

    long_approval = ActionResult(
        command_id="cmd-long-confirm",
        status="needs_user",
        summary="confirmation required",
        error=ActionError(
            code=TOOL_ERROR_CONFIRM_REQUIRED,
            message="Operator confirmation required.",
            details={"approval_id": "apr-99", "preview": "p" * 2400},
        ),
    )
    long_approval_msg = action_result_to_tool_message(
        "call-long-confirm", "git.push", long_approval
    )
    long_approval_payload = json.loads(long_approval_msg.content)
    assert long_approval_msg.tool_status == "blocked"
    assert long_approval_msg.tool_error == long_approval_payload["error"]
    assert long_approval_payload["truncation"] == {
        "truncated": True,
        "field_count": 1,
        "fields": [
            {
                "path": "error.details.preview",
                "kind": "string",
                "kept": TOOL_MESSAGE_STRING_LIMIT,
                "total": 2400,
            }
        ],
    }

    image_b64 = base64.b64encode(b"png" * 1000).decode("ascii")
    b64_result = ActionResult(
        command_id="cmd-shot-b64",
        status="success",
        summary="screenshot captured",
        outputs={
            "artifact": {
                "kind": "screenshot",
                "path": "",
                "mime": "image/png",
                "content_base64": image_b64,
            }
        },
    )
    b64_msg = action_result_to_tool_message("call-shot-2", "browser", b64_result)
    b64_payload = json.loads(b64_msg.content)
    assert [part.type for part in b64_msg.content_parts] == ["text", "image"]
    assert b64_msg.content_parts[1].data_base64 == image_b64
    assert b64_msg.meta["auto_vision_artifact"] is True
    assert b64_payload["truncation"] == {
        "truncated": True,
        "field_count": 1,
        "fields": [
            {
                "path": "outputs.artifact.content_base64",
                "kind": "string",
                "kept": TOOL_MESSAGE_STRING_LIMIT,
                "total": len(image_b64),
            }
        ],
    }


def test_agent_run_serializes_truncation_facts_into_outbound_provider_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    @tool(min_scope="READ_ONLY", idempotent=True)
    def dump_log() -> str:
        """Return a 5000-character synthetic log stream."""
        return "L" * 5000

    observed_tool_messages: list[dict[str, Any]] = []
    sent_call = False

    def handle(_transport: Any, request: httpx.Request) -> httpx.Response:
        nonlocal sent_call
        assert request.url.host == "truncation-probe.invalid"
        payload = json.loads(request.read())
        names = {
            spec["function"]["name"]
            for spec in payload.get("tools", [])
            if "function" in spec
        }
        tool_msgs = [
            msg
            for msg in payload.get("messages", [])
            if msg.get("role") == "tool" and msg.get("tool_call_id") == "call-log-1"
        ]
        if tool_msgs:
            observed_tool_messages.extend(tool_msgs)
        if "dump_log" in names and not sent_call:
            sent_call = True
            reply: dict[str, Any] = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call-log-1",
                        "type": "function",
                        "function": {"name": "dump_log", "arguments": "{}"},
                    }
                ],
            }
            finish = "tool_calls"
        else:
            reply = {"role": "assistant", "content": "log inspected"}
            finish = "stop"
        return httpx.Response(
            200,
            json={
                "id": "probe-1",
                "object": "chat.completion",
                "model": "local",
                "choices": [{"index": 0, "message": reply, "finish_reason": finish}],
            },
        )

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", handle)
    monkeypatch.setenv("TRUNCATION_PROBE_KEY", "local-fixture")
    config = OpenMinionConfig()
    _csc_install_default_agent(config, provider="openai")
    config.providers.openai.api_key_env = "TRUNCATION_PROBE_KEY"
    config.providers.openai.base_url = "https://truncation-probe.invalid/v1"
    config.action_policy.mode = "bypass"
    config.runtime.process_mode = "single-process"
    config.runtime.daemon_auto_start = False
    config.storage.path = str(tmp_path / "state.db")
    config_path = tmp_path / "agents.json"
    save_config(config, str(config_path))

    runtime = APIRuntime.from_config_path(
        str(config_path), home_root=tmp_path, data_root=tmp_path / "data"
    )
    try:
        answer = Agent(runtime=runtime, tools=[dump_log]).run(
            "Call dump_log once and confirm."
        )
    finally:
        runtime.close()

    assert answer.text.endswith("log inspected")
    assert len(observed_tool_messages) >= 1
    wire_payload = json.loads(observed_tool_messages[0]["content"])
    assert wire_payload["truncation"] == {
        "truncated": True,
        "field_count": 1,
        "fields": [
            {
                "path": "outputs.data",
                "kind": "string",
                "kept": 1200,
                "total": 5000,
            }
        ],
    }
