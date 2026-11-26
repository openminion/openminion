from pathlib import Path
from types import SimpleNamespace

from openminion.base.types import AgentResponse
from openminion.modules.brain.adapters.tool.runtime import (
    ToolAdapter,
    current_repository_root,
)
from openminion.services.brain.post_execution.mixin import _bind_tool_workspace_root
from tests.services.gateway._gateway_service_support import (
    GatewayServiceTestCase,
    asyncio,
)


class RepositorySkillScopeTests(GatewayServiceTestCase):
    def test_inbound_metadata_cannot_establish_or_replace_host_binding(self):
        roots = [Path(self._tmp.name).resolve() / name for name in ("a", "b")]
        for root in roots:
            (root / ".git").mkdir(parents=True)
        adapter = ToolAdapter(workspace_root=roots[0], artifactctl=SimpleNamespace())
        runtime = SimpleNamespace(sessions=self.sessions)
        observed = []

        class BindingProbe:
            async def run_turn(self, message, **_kwargs):
                with _bind_tool_workspace_root(
                    adapter,
                    message.metadata,
                    (),
                    runtime=runtime,
                    runtime_session_id=message.metadata["session_id"],
                ):
                    observed.append(current_repository_root())
                return AgentResponse(
                    text="binding checked",
                    channel=message.channel,
                    target=message.target,
                )

        self.gateway._turn_runner._agent = BindingProbe()
        session_id = "repository-boundary"
        try:
            for host_binding, expected in (
                (None, None),
                (str(roots[0]), str(roots[0])),
            ):
                if host_binding:
                    self.sessions.update_session_metadata(
                        session_id=session_id, patch={"repository_root": host_binding}
                    )
                asyncio.run(
                    self.gateway.run_once(
                        channel="console",
                        target="local-user",
                        message="read a skill",
                        session_id=session_id,
                        inbound_metadata={
                            "repository_root": str(roots[1]),
                            "workspace_root": str(roots[1]),
                            "cwd": str(roots[1]),
                        },
                        deliver=True,
                    )
                )
                self.assertEqual(observed[-1], expected)
                self.assertEqual(
                    self.sessions.get_session(session_id).metadata.get(
                        "repository_root"
                    ),
                    expected,
                )
                self.assertIsNone(current_repository_root())
        finally:
            adapter.close()
