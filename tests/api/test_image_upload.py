import json
from contextlib import closing
import threading
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from io import BytesIO
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest
from PIL import Image

from openminion.api.server.app import _OpenMinionAPIHandler
from openminion.api.server.images import handle_image_upload
from openminion.base.config import AgentProfileConfig, OpenMinionConfig
from openminion.modules.artifact.constants import MAX_IMAGE_BYTES
from openminion.modules.storage.runtime.session_store import SessionStore
from openminion.modules.storage.runtime.migrations import migrate_database
from openminion.modules.storage.runtime.sqlite import connect_database


@pytest.fixture
def runtime(tmp_path):
    path = tmp_path / "sessions.db"
    migrate_database(path)
    connection = connect_database(path)
    sessions = SessionStore(connection)
    config = OpenMinionConfig(
        agents={
            "agent": AgentProfileConfig(
                name="agent", provider="echo", default_channel="console"
            )
        },
        default_agent="agent",
    )
    runtime = SimpleNamespace(
        config=config,
        home_root=tmp_path,
        data_root=tmp_path / "data",
        sessions=sessions,
    )
    sessions.resolve_session(
        agent_id="agent", channel="console", target="owner", session_id="chat"
    )
    yield runtime
    connection.close()


def test_image_upload_http_requires_token_and_returns_scoped_ref(runtime):
    class Handler(_OpenMinionAPIHandler):
        ipc_token = "fixture-ipc-token"

    Handler.runtime = runtime
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    output = BytesIO()
    Image.new("RGB", (3, 4), "green").save(output, format="PNG")
    data = output.getvalue()
    path = "/v1/sessions/chat/images?" + urlencode(
        {"target": "owner", "channel": "console", "agent_id": "agent"}
    )
    try:
        for token, expected in (("wrong", 401), ("fixture-ipc-token", 201)):
            with closing(
                HTTPConnection(*server.server_address, timeout=5)
            ) as connection:
                connection.request(
                    "POST",
                    path,
                    body=data,
                    headers={"Content-Type": "image/png", "X-IPC-Token": token},
                )
                response = connection.getresponse()
                body = json.loads(response.read())
                assert response.status == expected, body
        assert body["image"]["ref"].startswith("image://")
        assert "path" not in body["image"]
        image_path = "/v1/sessions/chat/images/" + body["image"]["ref"][8:]
        query = "?target=owner&channel=console&agent_id=agent"
        for method, suffix, expected in (
            ("GET", "?target=other&channel=console&agent_id=agent", 400),
            ("GET", query, 200),
            ("DELETE", query, 200),
            ("GET", query, 400),
        ):
            with closing(
                HTTPConnection(*server.server_address, timeout=5)
            ) as connection:
                connection.request(
                    method,
                    image_path + suffix,
                    headers={"X-IPC-Token": "fixture-ipc-token"},
                )
                response = connection.getresponse()
                payload = response.read()
                assert response.status == expected, payload
                if method == "GET" and expected == 200:
                    assert response.getheader("Content-Type") == "image/png"
                    assert payload == data

    finally:
        server.shutdown()
        thread.join()
        server.server_close()


@pytest.mark.parametrize(
    "query,headers",
    [
        (
            "target=other&channel=console",
            {"Content-Length": "1", "Content-Type": "image/png"},
        ),
        (
            "target=owner&channel=other",
            {"Content-Length": "1", "Content-Type": "image/png"},
        ),
        (
            "target=owner&channel=console",
            {"Content-Length": str(MAX_IMAGE_BYTES + 1), "Content-Type": "image/png"},
        ),
        (
            "target=owner&channel=console",
            {"Content-Length": "1", "Content-Type": "image/svg+xml"},
        ),
        (
            "target=owner&channel=console",
            {
                "Content-Length": "1",
                "Content-Type": "image/png",
                "Transfer-Encoding": "chunked",
            },
        ),
    ],
)
def test_rejected_upload_does_not_read_body(runtime, query, headers):
    class Unreadable:
        def read(self, count):
            pytest.fail("Unauthorized or invalid upload read the body")

    results = []
    handler = SimpleNamespace(
        runtime=runtime,
        headers=headers,
        rfile=Unreadable(),
        _write_json=lambda *args: results.append(args),
    )
    handle_image_upload(handler, session_id="chat", query=query)
    assert results[0][0] == 400
