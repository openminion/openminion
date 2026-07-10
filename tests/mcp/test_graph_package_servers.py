from __future__ import annotations

import os
import sys
from pathlib import Path

from openminion.base.config.mcp import MCPServerConfig
from openminion.tools.mcp.manager import MCPFleetManager


REPO_ROOT = Path(__file__).resolve().parents[3]


def _pythonpath() -> str:
    paths = [
        REPO_ROOT / "sophiagraph" / "src",
        REPO_ROOT / "pragmagraph" / "src",
        REPO_ROOT / "graphfakos" / "src",
    ]
    current = os.environ.get("PYTHONPATH", "")
    tokens = [str(path) for path in paths]
    if current:
        tokens.append(current)
    return os.pathsep.join(tokens)


def _seed_repo(root: Path) -> None:
    (root / "src").mkdir(parents=True)
    (root / "README.md").write_text("# Runtime Graph\n", encoding="utf-8")
    (root / "src" / "app.py").write_text(
        "class RuntimeGraph:\n"
        "    pass\n\n"
        "def build_runtime_graph():\n"
        "    return RuntimeGraph()\n",
        encoding="utf-8",
    )


def _manager(tmp_path: Path) -> MCPFleetManager:
    repo = tmp_path / "pragmagraph-repo"
    _seed_repo(repo)
    env = {"PYTHONPATH": _pythonpath()}
    return MCPFleetManager(
        [
            MCPServerConfig(
                name="sophia",
                transport="stdio",
                command=[
                    sys.executable,
                    "-m",
                    "sophiagraph.server",
                    "serve-stdio",
                    "--backend",
                    "memory",
                ],
                env=env,
                cwd=str(REPO_ROOT),
                trusted=True,
                request_timeout_seconds=3.0,
                startup_timeout_seconds=3.0,
            ),
            MCPServerConfig(
                name="pragma",
                transport="stdio",
                command=[
                    sys.executable,
                    "-m",
                    "pragmagraph.server",
                    "serve-stdio",
                    "--root",
                    str(repo),
                ],
                env=env,
                cwd=str(REPO_ROOT),
                trusted=True,
                request_timeout_seconds=3.0,
                startup_timeout_seconds=3.0,
            ),
        ]
    )


def test_graph_package_servers_discover_and_call_over_mcp_stdio(
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path)
    try:
        tools = manager.discover_tools(parallel=True)
        remote_names = {(tool.server_name, tool.remote_name) for tool in tools}
        assert ("sophia", "knowledge_capabilities") in remote_names
        assert ("pragma", "pragmagraph_capabilities") in remote_names

        sophia = manager.call_tool(
            server_name="sophia",
            remote_name="knowledge_capabilities",
            arguments={},
        )
        pragma = manager.call_tool(
            server_name="pragma",
            remote_name="pragmagraph_capabilities",
            arguments={},
        )

        assert sophia["data"]["structured_content"]["backend"] == "memory"
        assert pragma["data"]["structured_content"]["service"]["startup_mode"] == "root"
    finally:
        manager.close()

    assert "sophiagraph.server" not in sys.modules
    assert "pragmagraph.server" not in sys.modules
