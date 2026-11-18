"""Selected images through real ingress, Brain, context and HTTP serialization."""

import base64
import json
import os
import re
import sqlite3
from pathlib import Path
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from threading import Thread

import pytest
from PIL import Image

from openminion.api.runtime import APIRuntime
from openminion.base.config import AgentProfileConfig, OpenMinionConfig, save_config
from openminion.modules.artifact.config import from_base_config
from openminion.modules.artifact.control import ArtifactCtl


@pytest.mark.parametrize(
    "mode,client,image_count",
    [
        (mode, client, count)
        for mode in ("native", "separate")
        for client in ("api", "cli", "focus")
        for count in (1, 2)
    ]
    + [("separate", "budget", 1)],
)
def test_selected_image_routes_and_api_restart(
    tmp_path, monkeypatch, mode, client, image_count
):
    if client == "budget":
        monkeypatch.setenv("OPENMINION_BRAIN_MAX_TOTAL_LLM_TOKENS", "10000")
    requests = []
    image_refs = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(request)
            reader = "Read the selected images in order" in json.dumps(
                request["messages"]
            )
            selected_refs = list(
                dict.fromkeys(
                    re.findall(
                        r"image://[0-9a-f-]{36}", json.dumps(request["messages"])
                    )
                )
            )
            answer = (
                json.dumps(
                    [
                        {
                            "image_ref": selected_ref,
                            "visible_text": "",
                            "observations": ["A blue rectangle"],
                            "uncertainties": [],
                        }
                        for selected_ref in selected_refs
                    ]
                )
                if reader
                else "A blue rectangle."
            )
            body = json.dumps(
                {
                    "id": "image-fixture",
                    "object": "chat.completion",
                    "model": request["model"],
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": answer},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": 5,
                        "total_tokens": 10000 if client == "budget" else 15,
                    },
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    config = OpenMinionConfig(
        default_agent="agent",
        agents={
            "agent": AgentProfileConfig(
                name="agent",
                provider="openai",
                default_channel="console",
                provider_config_overrides={
                    "model": "vision-fixture",
                    "enable_vision_input": mode == "native",
                },
                model_connections={
                    "vision": {
                        "provider": "openai",
                        "models": ["vision-fixture"],
                        "image_input": {"vision-fixture": "supported"},
                        "provider_config_overrides": {"enable_vision_input": True},
                    }
                },
                image_input={"mode": "native"}
                if mode == "native"
                else {
                    "mode": "separate",
                    "connection_id": "vision",
                    "model": "vision-fixture",
                },
            )
        },
    )
    config.providers.openai.api_key = "fixture-only"
    config.providers.openai.base_url = f"http://127.0.0.1:{server.server_port}/v1"
    config.runtime.process_mode = "single-process"
    config.runtime.daemon_auto_start = False
    config.storage.path = str(tmp_path / "state.db")
    config_path = tmp_path / "agents.json"
    save_config(config, str(config_path))
    runtime = APIRuntime.from_config_path(
        str(config_path), home_root=tmp_path, data_root=tmp_path / "data"
    )
    try:
        session = runtime.sessions.resolve_session(
            agent_id="agent", channel="console", target="owner", session_id="images"
        )
        image_paths = []
        with ArtifactCtl(
            from_base_config(
                base_config=config, home_root=tmp_path, data_root=tmp_path / "data"
            )
        ) as artifacts:
            for color in ["blue", "red"][:image_count]:
                image = BytesIO()
                Image.new("RGB", (12, 8), color).save(image, format="PNG")
                path = tmp_path / f"{color}.png"
                path.write_bytes(image.getvalue())
                image_paths.append(str(path))
                image_refs.append(
                    artifacts.ingest_image(
                        image.getvalue(),
                        mime="image/png",
                        session_id=session.image_scope,
                    ).ref
                )
        question = "What color is this?" if image_count == 1 else ""
        payload = {
            "session_id": session.id,
            "agent_id": "agent",
            "channel": "console",
            "target": "owner",
            "message": question,
            "image_refs": image_refs,
            "deliver": False,
            "inbound_metadata": {"caller_handles_delivery": "true", "resume": "true"},
            "timeout_seconds": 30,
        }
        if client == "focus":
            program = """
import asyncio, sys, shlex
from openminion.api.runtime import APIRuntime
from openminion.cli.interactive.runtime import OpenMinionRuntime
from openminion.cli.interactive.terminal.composer import TerminalComposer
from openminion.cli.transport.images import configure_images
async def main():
    rt = APIRuntime.from_config_path(sys.argv[1], home_root=sys.argv[2], data_root=sys.argv[3])
    try:
        focus = OpenMinionRuntime(rt, channel="console", target="focus-image-test", agent_id="agent", session_id="focus-images")
        composer = TerminalComposer(color=False)
        for path in sys.argv[6:]:
            composer.manage_images("/image add " + shlex.quote(path), working_dir=sys.argv[2])
        composer.manage_images("/image remove " + str(len(composer.image_paths)), working_dir=sys.argv[2])
        composer.manage_images("/image add " + shlex.quote(sys.argv[-1]), working_dir=sys.argv[2])
        configure_images(focus, "off")
        configure_images(focus, sys.argv[4] + " vision vision-fixture")
        async for chunk in focus.send_message(sys.argv[5], image_paths=composer.image_paths):
            print(chunk)
    finally:
        rt.close()
asyncio.run(main())
"""
            process = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    program,
                    str(config_path),
                    str(tmp_path),
                    str(tmp_path / "data"),
                    mode,
                    question,
                    *image_paths,
                ],
                capture_output=True,
                text=True,
                timeout=60,
                env={**os.environ, "PYTHONPATH": "src"},
            )
            assert process.returncode == 0, process.stderr + process.stdout
            assert "A blue rectangle" in process.stdout
            result = {}
        elif client == "cli":
            result_process = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "openminion",
                    "--config",
                    str(config_path),
                    "--home-root",
                    str(tmp_path),
                    "--data-root",
                    str(tmp_path / "data"),
                    "run",
                    "--session",
                    "cli-images",
                    *[part for path in image_paths for part in ("--image", path)],
                    "--json",
                    question,
                ],
                capture_output=True,
                text=True,
                timeout=60,
                env={**os.environ, "PYTHONPATH": "src"},
            )
            assert result_process.returncode == 0, (
                result_process.stderr + result_process.stdout
            )
            result = json.loads(result_process.stdout).get("turn", {})
        else:
            result = runtime.run_turn(payload=payload)
        if client == "budget":
            assert len(requests) == 1, requests
            assert "Read the selected images in order" in json.dumps(requests[0])
            return
        assert not result.get("errors"), result
        assert requests, result
        image_requests = [
            request
            for request in requests
            if '"type": "image_url"' in json.dumps(request)
        ]
        assert image_requests, requests
        parts = [
            part
            for message in image_requests[0]["messages"]
            if isinstance(message["content"], list)
            for part in message["content"]
            if part["type"] == "image_url"
        ]
        colors = [
            Image.open(
                BytesIO(base64.b64decode(part["image_url"]["url"].split(",", 1)[1]))
            ).getpixel((0, 0))
            for part in parts
        ]
        assert colors == [(0, 0, 255), (255, 0, 0)][:image_count]
        if mode == "separate":
            assert len(image_requests) == 1, requests
            assert image_requests[0].get("tools", []) == []
            assert image_requests[0].get("tool_choice", "none") == "none"
            assert any(
                "A blue rectangle" in json.dumps(request) for request in requests[1:]
            ), requests
        if client in {"cli", "focus"}:
            return
        from openminion.modules.brain.paths import resolve_brain_sessions_db_path

        with sqlite3.connect(
            resolve_brain_sessions_db_path(storage_path=Path(config.storage.path))
        ) as db:
            completed = [
                json.loads(row[0])
                for row in db.execute(
                    "SELECT payload_json FROM session_events WHERE event_type = 'llm.call.completed'"
                )
            ]
        assert len(completed) == len(requests), completed
        assert sum(event["usage"]["total_tokens"] for event in completed) == 15 * len(
            requests
        )
        assert sum(event.get("purpose") == "image_reader" for event in completed) == (
            mode == "separate"
        )
        mark = len(requests)
        runtime.close()
        runtime = APIRuntime.from_config_path(
            str(config_path), home_root=tmp_path, data_root=tmp_path / "data"
        )
        result = runtime.run_turn(
            payload={
                **payload,
                "message": "What did the image contain?",
                "image_refs": [],
            }
        )
        assert not result.get("errors"), result
        next_requests = requests[mark:]
        assert next_requests, result
        assert any(
            image_refs[0] in json.dumps(request)
            or "A blue rectangle" in json.dumps(request)
            for request in next_requests
        ), next_requests
        if mode == "native":
            assert any(
                '"type": "image_url"' in json.dumps(request)
                for request in next_requests
            ), next_requests
        if mode == "separate":
            assert all(
                '"type": "image_url"' not in json.dumps(request)
                for request in next_requests
            ), next_requests
    finally:
        runtime.close()
        server.shutdown()
        server.server_close()
        thread.join()
