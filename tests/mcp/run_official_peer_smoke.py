from __future__ import annotations

import argparse
import asyncio
import json
import sys
from importlib.metadata import version
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
HELPERS_ROOT = REPO_ROOT / "tests" / "helpers"
sys.path.insert(0, str(HELPERS_ROOT))
sys.path.insert(0, str(REPO_ROOT))

from runtime_roots import isolate_runtime_roots  # noqa: E402

isolate_runtime_roots(prefix="openminion-mcp-peer-")

from openminion.base.config.mcp import MCPServerConfig  # noqa: E402
from openminion.base.config.runtime import RuntimeConfig  # noqa: E402
from openminion.tools.mcp.manager import MCPFleetManager  # noqa: E402
from openminion.tools.mcp.server import (  # noqa: E402
    PublishedTool,
    serve_published_stdio,
)


EXPECTED_SDK_VERSION = "2.1.1"
SCRIPT_PATH = Path(__file__).resolve()


def _run_official_server() -> None:
    from mcp.server.mcpserver import MCPServer

    server = MCPServer("openminion-official-peer")

    @server.tool()
    def echo(text: str) -> str:
        return f"official:{text}"

    server.run(transport="stdio")


def _run_openminion_server() -> None:
    serve_published_stdio(
        [
            PublishedTool(
                name="openminion.echo",
                description="Echo text from the OpenMinion published server.",
                input_schema={
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                    "additionalProperties": False,
                },
                handler=lambda arguments: f"openminion:{arguments['text']}",
            )
        ],
        input_stream=sys.stdin,
        output_stream=sys.stdout,
    )


def _exercise_openminion_client() -> dict[str, str]:
    runtime = RuntimeConfig(
        mcp_servers=[
            MCPServerConfig(
                name="official-peer",
                transport="stdio",
                command=[sys.executable, str(SCRIPT_PATH), "--official-server"],
                request_timeout_seconds=10.0,
                startup_timeout_seconds=10.0,
            )
        ]
    )
    manager = MCPFleetManager.from_runtime_config(runtime)
    try:
        server_name = runtime.mcp_servers[0].name
        tools = manager.discover_tools()
        names = {tool.remote_name for tool in tools}
        if "echo" not in names:
            raise AssertionError(f"official SDK server did not expose echo: {names}")

        result = manager.call_tool(
            server_name=server_name,
            remote_name="echo",
            arguments={"text": "from-openminion"},
        )
        if result.get("ok") is not True:
            raise AssertionError(f"OpenMinion client tool call failed: {result}")
        if "official:from-openminion" not in str(result.get("content") or ""):
            raise AssertionError(f"unexpected official SDK server result: {result}")
        return {"server": server_name, "tool": "echo"}
    finally:
        manager.close()


async def _exercise_official_client() -> dict[str, str]:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    parameters = StdioServerParameters(
        command=sys.executable,
        args=[str(SCRIPT_PATH), "--openminion-server"],
    )
    async with stdio_client(parameters) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.discover()
            tools = await session.list_tools()
            names = {tool.name for tool in tools.tools}
            if "openminion.echo" not in names:
                raise AssertionError(
                    f"OpenMinion published server did not expose echo: {names}"
                )

            result = await session.call_tool(
                "openminion.echo",
                {"text": "from-official-sdk"},
            )
            if result.is_error:
                raise AssertionError(f"official SDK client tool call failed: {result}")
            text = "\n".join(
                item.text for item in result.content if hasattr(item, "text")
            )
            if "openminion:from-official-sdk" not in text:
                raise AssertionError(
                    f"unexpected OpenMinion published server result: {result}"
                )
            return {
                "protocol_version": session.protocol_version,
                "tool": "openminion.echo",
            }


def main() -> None:
    parser = argparse.ArgumentParser()
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--official-server", action="store_true")
    modes.add_argument("--openminion-server", action="store_true")
    args = parser.parse_args()

    if args.official_server:
        _run_official_server()
        return
    if args.openminion_server:
        _run_openminion_server()
        return

    installed_version = version("mcp")
    if installed_version != EXPECTED_SDK_VERSION:
        raise RuntimeError(
            f"official peer smoke requires mcp=={EXPECTED_SDK_VERSION}; "
            f"found {installed_version}"
        )

    summary = {
        "official_sdk_version": installed_version,
        "openminion_client": _exercise_openminion_client(),
        "official_client": asyncio.run(_exercise_official_client()),
    }
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
