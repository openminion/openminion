#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import time
from typing import Any


_ENABLE_CLIENT_REQUEST_TOOLS = str(
    os.environ.get("MOCK_MCP_ENABLE_CLIENT_REQUEST_TOOLS", "") or ""
).strip().lower() in {"1", "true", "yes", "on"}
_LSP_FRAMING = str(
    os.environ.get("MOCK_MCP_LSP_FRAMING", "") or ""
).strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
_EXIT_AFTER_LIST = str(
    os.environ.get("MOCK_MCP_EXIT_AFTER_LIST", "") or ""
).strip().lower() in {"1", "true", "yes", "on"}
_REQUIRED_CAPABILITIES = {
    str(token).strip().lower()
    for token in str(
        os.environ.get("MOCK_MCP_REQUIRE_CLIENT_CAPABILITIES", "") or ""
    ).split(",")
    if str(token).strip()
}
_INITIALIZE_DELAY_SECONDS = float(
    str(os.environ.get("MOCK_MCP_INITIALIZE_DELAY_SECONDS", "0") or "0")
)
_TOOLS_LIST_DELAY_SECONDS = float(
    str(os.environ.get("MOCK_MCP_TOOLS_LIST_DELAY_SECONDS", "0") or "0")
)
_LONG_TOOL_SECONDS = float(
    str(os.environ.get("MOCK_MCP_LONG_TOOL_SECONDS", "0") or "0")
)
_STDERR_BANNER = str(os.environ.get("MOCK_MCP_STDERR_BANNER", "") or "")

TOOLS = [
    {
        "name": "echo-text",
        "description": "Echo back a text payload.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "Text to echo.",
                }
            },
            "required": ["text"],
            "additionalProperties": False,
        },
    },
    {
        "name": "add-numbers",
        "description": "Add two integers.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "left": {"type": "integer"},
                "right": {"type": "integer"},
            },
            "required": ["left", "right"],
            "additionalProperties": False,
        },
    },
    {
        "name": "nullable-anyof",
        "description": "Nullable anyOf fixture.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "nickname": {
                    "anyOf": [{"type": "string"}, {"type": "null"}],
                }
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "tagged-union-simple",
        "description": "Tagged union fixture.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "payload": {
                    "anyOf": [
                        {
                            "type": "object",
                            "properties": {
                                "kind": {"const": "text"},
                                "text": {"type": "string"},
                            },
                            "required": ["kind", "text"],
                            "additionalProperties": False,
                        },
                        {
                            "type": "object",
                            "properties": {
                                "kind": {"const": "count"},
                                "count": {"type": "integer"},
                            },
                            "required": ["kind", "count"],
                            "additionalProperties": False,
                        },
                    ]
                }
            },
            "required": ["payload"],
            "additionalProperties": False,
        },
    },
    {
        "name": "unsupported-anyof",
        "description": "Unsupported schema fixture admitted via passthrough.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "payload": {
                    "anyOf": [{"type": "string"}, {"type": "integer"}],
                }
            },
        },
    },
    {
        "name": "sleep-tool",
        "description": "Long-running tool for progress and cancellation.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "seconds": {"type": "number"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "emit-list-changed",
        "description": "Emit list_changed and mutate the tool catalog.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    {
        "name": "stderr-error-tool",
        "description": "Emit stderr and return an error result.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
]
if _ENABLE_CLIENT_REQUEST_TOOLS:
    TOOLS.extend(
        [
            {
                "name": "request-roots",
                "description": "Request roots from the client.",
                "inputSchema": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            },
            {
                "name": "request-sampling",
                "description": "Request nested sampling from the client.",
                "inputSchema": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            },
            {
                "name": "request-elicitation",
                "description": "Request nested elicitation from the client.",
                "inputSchema": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            },
        ]
    )

PROMPTS = [
    {
        "name": "greet-user",
        "description": "Build a greeting prompt for a user.",
        "arguments": [
            {
                "name": "user_name",
                "description": "Name of the user to greet.",
                "required": True,
            }
        ],
    }
]

RESOURCES = [
    {
        "uri": "file://fixture/readme.md",
        "name": "readme-md",
        "description": "Fixture MCP README resource.",
        "mimeType": "text/markdown",
    }
]

RESOURCE_TEMPLATES = [
    {
        "uriTemplate": "file://fixture/{slug}.md",
        "name": "fixture-doc",
        "description": "Read a fixture markdown document by slug.",
        "mimeType": "text/markdown",
    }
]

_CANCELLED_REQUEST_IDS: set[str] = set()
_DYNAMIC_TOOL_ADDED = False


def _read_message() -> dict[str, Any] | None:
    line = sys.stdin.buffer.readline()
    if not line:
        return None
    if line.startswith(b"Content-Length"):
        headers: dict[str, str] = {}
        pending = line
        while True:
            if not pending:
                return None
            stripped = pending.strip()
            if not stripped:
                break
            key, value = pending.decode("utf-8").split(":", 1)
            headers[key.strip().lower()] = value.strip()
            pending = sys.stdin.buffer.readline()
        content_length = int(headers.get("content-length", "0") or "0")
        if content_length <= 0:
            return None
        payload = sys.stdin.buffer.read(content_length)
        if not payload:
            return None
        return json.loads(payload.decode("utf-8"))
    payload = line.strip()
    if not payload:
        return None
    return json.loads(payload.decode("utf-8"))


def _write_message(payload: dict[str, Any]) -> None:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    if _LSP_FRAMING:
        header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
        sys.stdout.buffer.write(header + body)
    else:
        sys.stdout.buffer.write(body + b"\n")
    sys.stdout.buffer.flush()


def _handle_request(request: dict[str, Any]) -> dict[str, Any] | None:
    global _DYNAMIC_TOOL_ADDED
    method = str(request.get("method", "") or "").strip()
    if method == "initialize":
        if _INITIALIZE_DELAY_SECONDS > 0:
            time.sleep(_INITIALIZE_DELAY_SECONDS)
        capabilities = (
            dict(request.get("params", {}) or {}).get("capabilities", {}) or {}
        )
        normalized_caps = {str(key).strip().lower() for key in capabilities.keys()}
        missing = sorted(_REQUIRED_CAPABILITIES - normalized_caps)
        if missing:
            return {
                "jsonrpc": "2.0",
                "id": request.get("id"),
                "error": {
                    "code": -32010,
                    "message": "missing client capabilities: " + ",".join(missing),
                },
            }
        return {
            "jsonrpc": "2.0",
            "id": request.get("id"),
            "result": {
                "protocolVersion": "2025-03-26",
                "capabilities": {"tools": {}, "prompts": {}, "resources": {}},
                "serverInfo": {"name": "mock-mcp-fixture", "version": "1.0.0"},
            },
        }
    if method == "notifications/initialized":
        return None
    if method == "notifications/cancelled":
        params = request.get("params", {}) or {}
        token = str(params.get("requestId", "") or "").strip()
        if token:
            _CANCELLED_REQUEST_IDS.add(token)
        return None
    if method == "tools/list":
        if _TOOLS_LIST_DELAY_SECONDS > 0:
            time.sleep(_TOOLS_LIST_DELAY_SECONDS)
        tools = list(TOOLS)
        if _DYNAMIC_TOOL_ADDED:
            tools.append(
                {
                    "name": "dynamic-after-change",
                    "description": "Dynamically added tool.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {},
                        "additionalProperties": False,
                    },
                }
            )
        return {
            "jsonrpc": "2.0",
            "id": request.get("id"),
            "result": {"tools": tools},
        }
    if method == "tools/call":
        params = request.get("params", {}) or {}
        name = str(params.get("name", "") or "").strip()
        arguments = params.get("arguments", {}) or {}
        request_id = str(request.get("id", "") or "").strip()
        if name == "echo-text":
            text = str(arguments.get("text", "") or "")
            return {
                "jsonrpc": "2.0",
                "id": request.get("id"),
                "result": {
                    "content": [{"type": "text", "text": f"echo: {text}"}],
                    "structuredContent": {"echo": text},
                    "isError": False,
                },
            }
        if name == "add-numbers":
            left = int(arguments.get("left", 0))
            right = int(arguments.get("right", 0))
            total = left + right
            return {
                "jsonrpc": "2.0",
                "id": request.get("id"),
                "result": {
                    "content": [{"type": "text", "text": str(total)}],
                    "structuredContent": {"total": total},
                    "isError": False,
                },
            }
        if name == "nullable-anyof":
            return {
                "jsonrpc": "2.0",
                "id": request.get("id"),
                "result": {
                    "content": [
                        {"type": "text", "text": json.dumps(arguments, sort_keys=True)}
                    ],
                    "structuredContent": dict(arguments),
                    "isError": False,
                },
            }
        if name == "tagged-union-simple":
            return {
                "jsonrpc": "2.0",
                "id": request.get("id"),
                "result": {
                    "content": [
                        {"type": "text", "text": json.dumps(arguments, sort_keys=True)}
                    ],
                    "structuredContent": dict(arguments),
                    "isError": False,
                },
            }
        if name == "unsupported-anyof":
            return {
                "jsonrpc": "2.0",
                "id": request.get("id"),
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": f"passthrough: {json.dumps(arguments, sort_keys=True)}",
                        }
                    ],
                    "structuredContent": dict(arguments),
                    "isError": False,
                },
            }
        if name == "sleep-tool":
            seconds = float(arguments.get("seconds", _LONG_TOOL_SECONDS or 1.0) or 1.0)
            steps = max(1, int(seconds / 0.1))
            for idx in range(steps):
                if request_id in _CANCELLED_REQUEST_IDS:
                    print("cancelled sleep-tool request", file=sys.stderr, flush=True)
                    return {
                        "jsonrpc": "2.0",
                        "id": request.get("id"),
                        "result": {
                            "content": [{"type": "text", "text": "cancelled"}],
                            "structuredContent": {"cancelled": True},
                            "isError": True,
                        },
                    }
                _write_message(
                    {
                        "jsonrpc": "2.0",
                        "method": "notifications/progress",
                        "params": {
                            "progressToken": request_id,
                            "progress": round((idx + 1) / steps, 3),
                            "message": f"step {idx + 1}/{steps}",
                        },
                    }
                )
                time.sleep(seconds / steps)
            return {
                "jsonrpc": "2.0",
                "id": request.get("id"),
                "result": {
                    "content": [{"type": "text", "text": "slept"}],
                    "structuredContent": {"slept": seconds},
                    "isError": False,
                },
            }
        if name == "emit-list-changed":
            _DYNAMIC_TOOL_ADDED = not _DYNAMIC_TOOL_ADDED
            _write_message(
                {
                    "jsonrpc": "2.0",
                    "method": "notifications/tools/list_changed",
                    "params": {},
                }
            )
            return {
                "jsonrpc": "2.0",
                "id": request.get("id"),
                "result": {
                    "content": [{"type": "text", "text": "changed"}],
                    "structuredContent": {"changed": True},
                    "isError": False,
                },
            }
        if name == "dynamic-after-change":
            return {
                "jsonrpc": "2.0",
                "id": request.get("id"),
                "result": {
                    "content": [{"type": "text", "text": "dynamic"}],
                    "structuredContent": {"dynamic": True},
                    "isError": False,
                },
            }
        if name == "stderr-error-tool":
            if _STDERR_BANNER:
                print(_STDERR_BANNER, file=sys.stderr, flush=True)
            return {
                "jsonrpc": "2.0",
                "id": request.get("id"),
                "result": {
                    "content": [{"type": "text", "text": "stderr failure"}],
                    "structuredContent": {"stderr_failure": True},
                    "isError": True,
                },
            }
        if name == "request-roots":
            roots_result = _request_client("roots/list", {}, nested_id="roots-1")
            roots = roots_result.get("roots", [])
            root_names = [
                str(item.get("name", "") or item.get("uri", "") or "").strip()
                for item in roots
                if isinstance(item, dict)
            ]
            return {
                "jsonrpc": "2.0",
                "id": request.get("id"),
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": "roots: "
                            + ", ".join(name for name in root_names if name),
                        }
                    ],
                    "structuredContent": {"roots": roots},
                    "isError": False,
                },
            }
        if name == "request-sampling":
            sampling_result = _request_client(
                "sampling/createMessage",
                {
                    "messages": [
                        {
                            "role": "user",
                            "content": {
                                "type": "text",
                                "text": "Say hello from sampling.",
                            },
                        }
                    ],
                    "maxTokens": 32,
                },
                nested_id="sampling-1",
            )
            sampled_text = _sampling_text(sampling_result.get("content"))
            return {
                "jsonrpc": "2.0",
                "id": request.get("id"),
                "result": {
                    "content": [{"type": "text", "text": f"sampling: {sampled_text}"}],
                    "structuredContent": {"sampling_result": sampling_result},
                    "isError": False,
                },
            }
        if name == "request-elicitation":
            elicitation_result = _request_client(
                "elicitation/create",
                {
                    "mode": "form",
                    "message": "Please provide a display name.",
                    "requestedSchema": {
                        "type": "object",
                        "properties": {"display_name": {"type": "string"}},
                        "required": ["display_name"],
                        "additionalProperties": False,
                    },
                },
                nested_id="elicitation-1",
            )
            action = str(elicitation_result.get("action", "") or "").strip()
            content = elicitation_result.get("content", {}) or {}
            display_name = ""
            if isinstance(content, dict):
                display_name = str(content.get("display_name", "") or "").strip()
            summary = f"elicitation: {action}"
            if display_name:
                summary += f" ({display_name})"
            return {
                "jsonrpc": "2.0",
                "id": request.get("id"),
                "result": {
                    "content": [{"type": "text", "text": summary}],
                    "structuredContent": {"elicitation_result": elicitation_result},
                    "isError": False,
                },
            }
        if _STDERR_BANNER:
            print(_STDERR_BANNER, file=sys.stderr, flush=True)
        return {
            "jsonrpc": "2.0",
            "id": request.get("id"),
            "result": {
                "content": [{"type": "text", "text": f"unknown tool: {name}"}],
                "structuredContent": {"tool": name},
                "isError": True,
            },
        }
    if method == "prompts/list":
        return {
            "jsonrpc": "2.0",
            "id": request.get("id"),
            "result": {"prompts": PROMPTS},
        }
    if method == "prompts/get":
        params = request.get("params", {}) or {}
        name = str(params.get("name", "") or "").strip()
        arguments = params.get("arguments", {}) or {}
        if name == "greet-user":
            user_name = str(arguments.get("user_name", "") or "").strip() or "friend"
            return {
                "jsonrpc": "2.0",
                "id": request.get("id"),
                "result": {
                    "description": "Greeting prompt.",
                    "messages": [
                        {
                            "role": "user",
                            "content": {"type": "text", "text": f"Hello, {user_name}!"},
                        }
                    ],
                },
            }
        return {
            "jsonrpc": "2.0",
            "id": request.get("id"),
            "error": {"code": -32001, "message": f"Unknown prompt: {name}"},
        }
    if method == "resources/list":
        return {
            "jsonrpc": "2.0",
            "id": request.get("id"),
            "result": {"resources": RESOURCES},
        }
    if method == "resources/templates/list":
        return {
            "jsonrpc": "2.0",
            "id": request.get("id"),
            "result": {"resourceTemplates": RESOURCE_TEMPLATES},
        }
    if method == "resources/read":
        params = request.get("params", {}) or {}
        uri = str(params.get("uri", "") or "").strip()
        if uri in {"file://fixture/readme.md", "file://fixture/dynamic.md"}:
            return {
                "jsonrpc": "2.0",
                "id": request.get("id"),
                "result": {
                    "contents": [
                        {
                            "uri": uri,
                            "mimeType": "text/markdown",
                            "text": (
                                "# Fixture README\n\nMCP fixture resource body."
                                if uri.endswith("readme.md")
                                else "# Dynamic\n\nMCP fixture template body."
                            ),
                        }
                    ]
                },
            }
        return {
            "jsonrpc": "2.0",
            "id": request.get("id"),
            "error": {"code": -32002, "message": f"Unknown resource: {uri}"},
        }
    return {
        "jsonrpc": "2.0",
        "id": request.get("id"),
        "error": {"code": -32601, "message": f"Unknown method: {method}"},
    }


def _request_client(
    method: str, params: dict[str, Any], *, nested_id: str
) -> dict[str, Any]:
    _write_message(
        {
            "jsonrpc": "2.0",
            "id": nested_id,
            "method": method,
            "params": params,
        }
    )
    response = _read_message()
    if response is None:
        raise RuntimeError(f"client closed during nested {method} request")
    if response.get("id") != nested_id:
        raise RuntimeError(f"unexpected nested response id for {method}")
    error = response.get("error")
    if isinstance(error, dict):
        raise RuntimeError(str(error.get("message", "") or error.get("code") or method))
    result = response.get("result")
    if not isinstance(result, dict):
        raise RuntimeError(f"nested {method} result was not an object")
    return dict(result)


def _sampling_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, dict):
        text = str(content.get("text", "") or "").strip()
        if text:
            return text
        for value in content.values():
            nested = _sampling_text(value)
            if nested:
                return nested
        return ""
    if isinstance(content, list):
        for item in content:
            nested = _sampling_text(item)
            if nested:
                return nested
        return ""
    return ""


def main() -> int:
    while True:
        request = _read_message()
        if request is None:
            return 0
        response = _handle_request(request)
        if response is not None:
            _write_message(response)
            if (
                _EXIT_AFTER_LIST
                and str(request.get("method", "") or "").strip() == "tools/list"
            ):
                return 0


if __name__ == "__main__":
    raise SystemExit(main())
