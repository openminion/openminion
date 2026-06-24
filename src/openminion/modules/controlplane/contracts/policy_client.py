from dataclasses import dataclass
from typing import Any


@dataclass
class PolicyRequest:
    request_id: str
    tool: str
    method: str
    subject_id: str
    session_id: str
    trace_id: str
    status: str  # pending/approved/denied
    created_at: str
    expires_at: str | None = None
    grant_id: str | None = None


class PolicyClient:
    """Client for communicating with openminion-policy."""

    def __init__(self, api_url: str = "http://localhost:5011") -> None:
        self._api_url = api_url.rstrip("/")

    async def get_pending_requests(
        self, session_id: str | None = None
    ) -> list[PolicyRequest]:
        """Get pending policy approval requests."""
        import aiohttp

        try:
            params = {"session_id": session_id} if session_id else {}
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self._api_url}/policy/requests",
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as resp:
                    if resp.status != 200:
                        return []
                    data = await resp.json()
                    return [
                        PolicyRequest(
                            request_id=row.get("request_id", ""),
                            tool=row.get("tool", ""),
                            method=row.get("method", ""),
                            subject_id=row.get("subject_id", ""),
                            session_id=row.get("session_id", ""),
                            trace_id=row.get("trace_id", ""),
                            status=row.get("status", "pending"),
                            created_at=row.get("created_at", ""),
                            expires_at=row.get("expires_at"),
                            grant_id=row.get("grant_id"),
                        )
                        for row in data.get("requests", [])
                    ]
        except Exception:
            return []

    async def approve_request(
        self,
        request_id: str,
        action: str = "once",
        until_seconds: int | None = None,
    ) -> bool:
        """Approve a policy request."""
        import aiohttp

        try:
            payload = {"action": action}
            if until_seconds:
                payload["until_seconds"] = until_seconds
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self._api_url}/policy/requests/{request_id}/approve",
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as resp:
                    return resp.status in (200, 201)
        except Exception:
            return False

    async def deny_request(self, request_id: str) -> bool:
        """Deny a policy request."""
        import aiohttp

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self._api_url}/policy/requests/{request_id}/deny",
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as resp:
                    return resp.status in (200, 201)
        except Exception:
            return False

    async def list_grants(self, session_id: str | None = None) -> list[dict[str, Any]]:
        """List active grants."""
        import aiohttp

        try:
            params = {"session_id": session_id} if session_id else {}
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self._api_url}/policy/grants",
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as resp:
                    if resp.status != 200:
                        return []
                    data = await resp.json()
                    return data.get("grants", [])
        except Exception:
            return []
