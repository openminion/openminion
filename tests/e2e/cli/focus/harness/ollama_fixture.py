from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
from typing import Any


class OllamaFixtureHandler(BaseHTTPRequestHandler):
    requests: list[dict[str, Any]] = []
    turn_response_index = 0
    turn_response_messages: tuple[dict[str, Any], ...] = ()

    def log_message(self, format: str, *args: object) -> None:
        del format, args

    def do_POST(self) -> None:
        if self.path != "/api/chat":
            self.send_error(404)
            return
        content_length = int(self.headers.get("Content-Length", "0"))
        request_payload = (
            json.loads(self.rfile.read(content_length)) if content_length else {}
        )
        type(self).requests.append(request_payload)
        messages = request_payload["messages"]
        last_content = str(messages[-1].get("content", ""))
        schema_title = request_payload.get("format", {}).get("title", "")
        if last_content == "Reply with exactly: openminion provider check ok":
            response_message = {
                "role": "assistant",
                "content": "openminion provider check ok",
            }
        elif schema_title == "UserMessageCandidateReport":
            response_message = {"role": "assistant", "content": '{"items":[]}'}
        elif schema_title == "FreshnessContract":
            response_message = {
                "role": "assistant",
                "content": (
                    '{"intent":"workspace listing","domain":"general",'
                    '"time_sensitive":false,"needs_live_data":false,'
                    '"needs_sources":false,"needs_exact_date":false,'
                    '"answer_mode":"local_only","reason":"local workspace",'
                    '"confidence":1.0}'
                ),
            }
        else:
            response_message = type(self).turn_response_messages[
                type(self).turn_response_index
            ]
            type(self).turn_response_index += 1
        payload = json.dumps(
            {
                "model": "qwen2.5:14b",
                "message": response_message,
                "done": True,
                "done_reason": (
                    "tool_calls" if response_message.get("tool_calls") else "stop"
                ),
                "prompt_eval_count": 1,
                "eval_count": 1,
            }
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


@contextmanager
def ollama_fixture_server(
    turn_response_messages: tuple[dict[str, Any], ...],
) -> Iterator[tuple[str, list[dict[str, Any]]]]:
    OllamaFixtureHandler.requests = []
    OllamaFixtureHandler.turn_response_index = 0
    OllamaFixtureHandler.turn_response_messages = turn_response_messages
    server = ThreadingHTTPServer(("127.0.0.1", 0), OllamaFixtureHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", OllamaFixtureHandler.requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


__all__ = ["ollama_fixture_server"]
