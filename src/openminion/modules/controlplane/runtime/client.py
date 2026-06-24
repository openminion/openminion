import asyncio
from dataclasses import dataclass

from ..interfaces import CONTROLPLANE_INTERFACE_VERSION


@dataclass
class RunStatus:
    run_id: str
    state: str
    agent_id: str
    session_id: str
    started_at: str | None = None
    completed_at: str | None = None
    duration_ms: int = 0
    error_count: int = 0


class RuntimeClient:
    """Client for communicating with openminion-runtime."""

    contract_version = CONTROLPLANE_INTERFACE_VERSION

    def __init__(self, api_url: str = "http://localhost:5010") -> None:
        self._api_url = api_url.rstrip("/")
        self._session_id: str | None = None

    def set_session(self, session_id: str) -> None:
        self._session_id = session_id

    async def get_run_status(self, run_id: str) -> RunStatus | None:
        """Get status of a specific run."""
        import aiohttp

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self._api_url}/runs/{run_id}",
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as resp:
                    if resp.status == 404:
                        return None
                    if resp.status != 200:
                        return RunStatus(
                            run_id=run_id,
                            state="error",
                            agent_id="",
                            session_id="",
                        )
                    data = await resp.json()
                    return RunStatus(
                        run_id=data.get("run_id", run_id),
                        state=data.get("state", "unknown"),
                        agent_id=data.get("agent_id", ""),
                        session_id=data.get("session_id", ""),
                        started_at=data.get("started_at"),
                        completed_at=data.get("completed_at"),
                        duration_ms=data.get("duration_ms", 0),
                        error_count=data.get("error_count", 0),
                    )
        except Exception:
            return None

    async def list_runs(self, session_id: str | None = None) -> list[RunStatus]:
        """List active runs."""
        import aiohttp

        sid = session_id or self._session_id
        if not sid:
            return []
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self._api_url}/sessions/{sid}/runs",
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as resp:
                    if resp.status != 200:
                        return []
                    data = await resp.json()
                    return [
                        RunStatus(
                            run_id=row.get("run_id", ""),
                            state=row.get("state", "unknown"),
                            agent_id=row.get("agent_id", ""),
                            session_id=sid,
                            started_at=row.get("started_at"),
                            duration_ms=row.get("duration_ms", 0),
                            error_count=row.get("error_count", 0),
                        )
                        for row in data.get("runs", [])
                    ]
        except Exception:
            return []

    async def cancel_run(self, run_id: str) -> bool:
        """Cancel a running turn."""
        import aiohttp

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self._api_url}/runs/{run_id}/cancel",
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as resp:
                    return resp.status in (200, 202)
        except Exception:
            return False


class SyncRuntimeClient:
    """Synchronous wrapper for RuntimeClient."""

    contract_version = CONTROLPLANE_INTERFACE_VERSION

    def __init__(self, api_url: str = "http://localhost:5010") -> None:
        self._client = RuntimeClient(api_url)

    def _run(self, coro):
        return asyncio.get_event_loop().run_until_complete(coro)

    def set_session(self, session_id: str) -> None:
        self._client.set_session(session_id)

    def get_run_status(self, run_id: str) -> RunStatus | None:
        return self._run(self._client.get_run_status(run_id))

    def list_runs(self, session_id: str | None = None) -> list[RunStatus]:
        return self._run(self._client.list_runs(session_id))

    def cancel_run(self, run_id: str) -> bool:
        return self._run(self._client.cancel_run(run_id))
