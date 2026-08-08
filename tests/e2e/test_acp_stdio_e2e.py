from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest
from acp import spawn_agent_process
from acp.schema import (
    AllowedOutcome,
    RequestPermissionResponse,
    TextContentBlock,
)

pytestmark = pytest.mark.e2e


class _Client:
    def __init__(self) -> None:
        self.updates: list[object] = []

    async def session_update(
        self, session_id: str, update: object, **_kwargs: object
    ) -> None:
        del session_id
        self.updates.append(update)

    async def request_permission(
        self,
        session_id: str,
        tool_call: object,
        options: list[object],
        **_kwargs: object,
    ) -> RequestPermissionResponse:
        del session_id, tool_call, options
        return RequestPermissionResponse(
            outcome=AllowedOutcome(outcome="selected", option_id="allow_once")
        )


def _write_echo_config(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "agents": {
                    "acp-agent": {
                        "name": "acp-agent",
                        "provider": "echo",
                        "default_channel": "console",
                    }
                },
                "default_agent": "acp-agent",
                "enabled_channels": ["console"],
                "runtime": {
                    "demo_mode": True,
                    "process_mode": "single-process",
                },
            }
        ),
        encoding="utf-8",
    )


def _agent_args(config_path: Path, data_root: Path) -> tuple[str, ...]:
    return (
        "-m",
        "openminion.cli.main",
        "--config",
        str(config_path),
        "--data-root",
        str(data_root),
        "acp",
    )


def _agent_env() -> dict[str, str]:
    return {
        **os.environ,
        "PYTHONPATH": str(Path(__file__).parents[2] / "src"),
        "PYTHONDONTWRITEBYTECODE": "1",
    }


@pytest.mark.asyncio
async def test_local_acp_stdio_lifecycle_with_independent_client(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_root = tmp_path / "data"
    config_path = tmp_path / "config.json"
    _write_echo_config(config_path)
    client = _Client()

    async with spawn_agent_process(
        client,
        sys.executable,
        *_agent_args(config_path, data_root),
        env=_agent_env(),
        cwd=workspace,
        transport_kwargs={"stderr": None},
    ) as (connection, process):
        initialized = await connection.initialize(protocol_version=1)
        created = await connection.new_session(cwd=str(workspace))
        response = await connection.prompt(
            created.session_id,
            [TextContentBlock(type="text", text="ACP end-to-end proof")],
        )
        listed = await connection.list_sessions(cwd=str(workspace))
        await connection.load_session(cwd=str(workspace), session_id=created.session_id)
        await connection.close_session(created.session_id)

        assert initialized.protocol_version == 1
        assert response.stop_reason == "end_turn"
        assert [item.session_id for item in listed.sessions] == [created.session_id]
        assert any(
            getattr(getattr(update, "content", None), "text", "")
            for update in client.updates
        )
        assert process.returncode is None


@pytest.mark.asyncio
async def test_local_acp_stdout_is_json_rpc_and_errors_are_typed(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path = tmp_path / "config.json"
    data_root = tmp_path / "data"
    _write_echo_config(config_path)
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        *_agent_args(config_path, data_root),
        cwd=workspace,
        env=_agent_env(),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    payload = "\n".join(
        [
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": 1,
                        "clientCapabilities": {},
                        "clientInfo": {"name": "test-client", "version": "1"},
                    },
                }
            ),
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "openminion/unknown",
                    "params": {},
                }
            ),
            "{not-json",
            "",
        ]
    ).encode()
    stdout, _stderr = await asyncio.wait_for(
        process.communicate(input=payload), timeout=15
    )

    messages = [json.loads(line) for line in stdout.splitlines()]
    assert process.returncode == 0
    assert messages[0]["id"] == 1
    assert messages[1]["error"]["code"] == -32601
    assert len(messages) == 2
    assert "Error parsing JSON-RPC message" in _stderr.decode()
