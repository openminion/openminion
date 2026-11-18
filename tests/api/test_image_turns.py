"""Selected images through real ingress, Brain, context and HTTP serialization."""

import json
import os
import re
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


@pytest.mark.parametrize("mode", ["native", "separate"])
@pytest.mark.parametrize("client", ["api", "cli", "focus"])
def test_selected_image_reaches_provider_and_survives_next_turn(tmp_path, mode, client):
    requests = []
    image_ref = ""

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(request)
            reader = "Read the selected images in order" in json.dumps(
                request["messages"]
            )
            selected_ref = re.search(
                r"image://[0-9a-f-]{36}", json.dumps(request["messages"])
            )
            answer = (
                json.dumps(
                    [
                        {
                            "image_ref": selected_ref.group()
                            if selected_ref
                            else image_ref,
                            "visible_text": "",
                            "observations": ["A blue rectangle"],
                            "uncertainties": [],
                        }
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
                        "total_tokens": 15,
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
        image = BytesIO()
        Image.new("RGB", (12, 8), "blue").save(image, format="PNG")
        with ArtifactCtl(
            from_base_config(
                base_config=config, home_root=tmp_path, data_root=tmp_path / "data"
            )
        ) as artifacts:
            image_ref = artifacts.ingest_image(
                image.getvalue(), mime="image/png", session_id=session.image_scope
            ).ref
        payload = {
            "session_id": session.id,
            "agent_id": "agent",
            "channel": "console",
            "target": "owner",
            "message": "What color is this?",
            "image_refs": [image_ref],
            "deliver": False,
            "inbound_metadata": {"caller_handles_delivery": "true", "resume": "true"},
            "timeout_seconds": 30,
        }
        if client == "focus":
            image_path = tmp_path / "selected.png"
            image_path.write_bytes(image.getvalue())
            program = """
import asyncio, sys
from openminion.api.runtime import APIRuntime
from openminion.cli.interactive.runtime import OpenMinionRuntime
async def main():
    rt = APIRuntime.from_config_path(sys.argv[1], home_root=sys.argv[2], data_root=sys.argv[3])
    try:
        focus = OpenMinionRuntime(rt, channel="console", target="focus-image-test", agent_id="agent", session_id="focus-images")
        async for chunk in focus.send_message("What color is this?", image_paths=[sys.argv[4]]):
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
                    str(image_path),
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
            image_path = tmp_path / "selected.png"
            image_path.write_bytes(image.getvalue())
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
                    "--image",
                    str(image_path),
                    "--json",
                    "What color is this?",
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
        assert not result.get("errors"), result
        assert requests, result
        image_requests = [
            request
            for request in requests
            if '"type": "image_url"' in json.dumps(request)
        ]
        assert image_requests, requests
        if mode == "separate":
            assert len(image_requests) == 1, requests
            assert image_requests[0].get("tools", []) == []
            assert image_requests[0].get("tool_choice", "none") == "none"
            assert any(
                "A blue rectangle" in json.dumps(request) for request in requests[1:]
            ), requests
        if client in {"cli", "focus"}:
            return
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
            image_ref in json.dumps(request)
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
