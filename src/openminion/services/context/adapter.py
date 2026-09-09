import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, cast

from openminion.base.config.runtime import resolve_identity_db_from_env
from openminion.services.config import resolve_services_env, resolve_services_path
from openminion.services.bootstrap.paths import (
    SERVICES_STATE_DB_FILENAME,
    SERVICES_STATE_DIRNAME,
)
from openminion.services.context.constants import (
    CONTEXTCTL_DUAL_RENDER_ENV,
    CONTEXTCTL_GATEWAY_ENABLED_ENV,
    OPENMINION_SESSION_CONTEXT_TOKEN_BUDGET_ENV,
)
from openminion.modules.context.pack.semantics import resolve_context_total_token_budget
from openminion.modules.context.memory_client import NullMemoryClient
from openminion.modules.context.slices import (
    RuntimeMappedSessionClient as _RuntimeMappedSessionClient,
)

_logger = logging.getLogger(__name__)


@dataclass
class ContextCtlMessage:
    role: str
    content: str


class ContextCtlGatewayAdapter:
    """Module-first ContextCtl adapter for the OpenMinion gateway."""

    def __init__(
        self,
        *,
        contextctl_dual_render: bool = False,
        enabled: bool = True,
        agent_id: str = "",
        runtime_token_budget: int = 0,
        session_client: Any | None = None,
        memory_client: Any | None = None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self._enabled = enabled
        self._dual_render = contextctl_dual_render
        self._agent_id = agent_id
        self._runtime_token_budget = max(0, runtime_token_budget)
        self._session_client = session_client
        self._memory_client = memory_client
        self._service: Any | None = None
        self._identity_ctl: Any | None = None
        self._owned_session_client: _RuntimeMappedSessionClient | None = None
        self._log = logger or _logger
        if self._dual_render:
            self._log.warning(
                "%s=true; ContextCtl history parity logging is enabled.",
                CONTEXTCTL_DUAL_RENDER_ENV,
            )

    @classmethod
    def from_env(
        cls,
        *,
        agent_id: str = "",
        runtime_token_budget: int | None = None,
        session_client: Any | None = None,
        memory_client: Any | None = None,
        logger: Optional[logging.Logger] = None,
    ) -> "ContextCtlGatewayAdapter":
        """Construct adapter from environment variable flags."""
        env_config = resolve_services_env()

        def _int_env(name: str) -> int:
            raw = env_config.get(name, "").strip()
            if not raw:
                return 0
            try:
                return max(0, int(raw))
            except ValueError:
                return 0

        return cls(
            enabled=env_config.get_bool(CONTEXTCTL_GATEWAY_ENABLED_ENV, False),
            contextctl_dual_render=env_config.get_bool(
                CONTEXTCTL_DUAL_RENDER_ENV, False
            ),
            agent_id=agent_id,
            runtime_token_budget=(
                _int_env(OPENMINION_SESSION_CONTEXT_TOKEN_BUDGET_ENV)
                if runtime_token_budget is None
                else runtime_token_budget
            ),
            session_client=session_client,
            memory_client=memory_client,
            logger=logger,
        )

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    @property
    def is_dual_render(self) -> bool:
        return self._dual_render

    def select_history(
        self,
        *,
        history: list[object],
        session_id: str,
        agent_id: str,
        query: str,
        contextctl_messages: Optional[list[ContextCtlMessage]] = None,
    ) -> list[object]:
        """C-09: Select which history to pass to the agent."""
        if self._dual_render and contextctl_messages is not None:
            self._log_parity(
                history_count=len(history),
                contextctl_count=len(contextctl_messages),
                session_id=session_id,
                agent_id=agent_id,
            )

        if contextctl_messages is not None:
            converted = self._contextctl_to_history(contextctl_messages)
            self._log.debug(
                "context_adapter: using contextctl messages count=%d session_id=%s",
                len(converted),
                session_id,
            )
            return converted

        return history

    def build_ctxctl_messages(
        self,
        *,
        session_id: str,
        agent_id: str,
        query: str,
        purpose: str = "act",
    ) -> Optional[list[ContextCtlMessage]]:
        """C-08: Attempt to build context pack via ctxctl."""
        try:
            return self._call_ctxctl(
                session_id=session_id,
                agent_id=agent_id,
                query=query,
                purpose=purpose,
            )
        except Exception as exc:
            self._log.warning(
                "context_adapter: ctxctl build_pack failed session_id=%s error=%s; "
                "falling back to provided history",
                session_id,
                exc,
            )
            return None

    def _call_ctxctl(
        self,
        *,
        session_id: str,
        agent_id: str,
        query: str,
        purpose: str,
    ) -> list[ContextCtlMessage]:
        """Build a context pack through the ContextCtl service."""
        from openminion.modules.context.schemas import (
            BuildPackRequest,
            Purpose,
            default_budgets_for,
        )

        service = self._ensure_service(agent_id)
        budgets_override = default_budgets_for(cast(Purpose, purpose))
        budgets_override.total_max_tokens = resolve_context_total_token_budget(
            purpose=purpose,
            runtime_token_budget=self._runtime_token_budget,
            requested_token_budget=None,
        )
        pack = service.build_pack(
            BuildPackRequest(
                session_id=session_id,
                agent_id=agent_id,
                purpose=cast(Purpose, purpose),
                query=query,
                budgets_override=budgets_override,
            )
        )
        return [
            ContextCtlMessage(role=m.role, content=m.content)
            for m in pack.messages
            if m.content.strip()
        ]

    def _ensure_service(self, agent_id: str) -> Any:
        if self._service is not None:
            return self._service

        from openminion.modules.context.contracts import IdentityClient
        from openminion.modules.context.service import ContextCtlService

        identity_ctl: Any | None = None
        identity_store: Any | None = None
        try:
            try:
                from openminion.modules.identity.storage.store import (
                    SQLiteIdentityStore,
                )
                from openminion.modules.identity.runtime.service import IdentityCtl
                from openminion.services.identity.bootstrap import (
                    ensure_default_profile,
                )

                db_path = str(resolve_identity_db_from_env(env=resolve_services_env()))

                identity_store = SQLiteIdentityStore(sqlite_path=db_path)
                identity_ctl = IdentityCtl(store=identity_store)
                identity_store = None
                ensure_default_profile(identity_ctl, agent_id, "")
            except ImportError:
                identity_ctl = _EchoIdentityClient(agent_id=agent_id)

            identity_client = cast(IdentityClient, identity_ctl)
            session_client = self._session_client
            if session_client is None:
                self._owned_session_client = _RuntimeMappedSessionClient(
                    sqlite_path=_resolve_runtime_sqlite_path()
                )
                session_client = self._owned_session_client
            memory_stub = self._memory_client or NullMemoryClient()
            self._service = ContextCtlService(
                identityctl=identity_client,
                sessctl=session_client,
                memctl=memory_stub,
                artifactctl=_NullArtifactClient(),
            )
            self._identity_ctl = identity_ctl
            return self._service
        except Exception:
            if identity_ctl is not None:
                identity_ctl.close()
            elif identity_store is not None:
                identity_store.close()
            if self._owned_session_client is not None:
                self._owned_session_client.close()
                self._owned_session_client = None
            raise

    def release_session(self, session_id: str) -> None:
        if self._service is not None:
            self._service.release_session(session_id)

    def close(self) -> None:
        if self._service is not None:
            self._service.close()
            self._service = None
        if self._identity_ctl is not None:
            self._identity_ctl.close()
            self._identity_ctl = None
        if self._owned_session_client is not None:
            self._owned_session_client.close()
            self._owned_session_client = None

    def _contextctl_to_history(self, messages: list[ContextCtlMessage]) -> list[object]:
        from openminion.base.types import Message

        return [
            Message(
                channel="contextctl",
                target="",
                body=msg.content,
                metadata={"role": msg.role, "source": "contextctl"},
            )
            for msg in messages
        ]

    def _log_parity(
        self,
        *,
        history_count: int,
        contextctl_count: int,
        session_id: str,
        agent_id: str,
    ) -> None:
        self._log.info(
            "context_adapter dual_render: "
            "session_id=%s agent_id=%s history_count=%d contextctl_count=%d delta=%d",
            session_id,
            agent_id,
            history_count,
            contextctl_count,
            abs(history_count - contextctl_count),
        )


class _EchoIdentityClient:
    contract_version = "v1"

    def __init__(self, agent_id: str) -> None:
        self._agent_id = agent_id

    def render(
        self,
        *,
        agent_id: str,
        purpose: str,
        max_tokens: int,
        provider_pref: str | None = None,
        query_text: str | None = None,
    ) -> Any:
        del purpose, max_tokens, provider_pref, query_text
        from openminion.modules.context.schemas import IdentitySnippet

        return IdentitySnippet(
            agent_id=agent_id,
            profile_version="adapter:v0",
            render_version="adapter:v0",
            text=f"Agent: {agent_id}",
        )

    def close(self) -> None:
        return None


class _NullArtifactClient:
    contract_version = "v1"

    def query_digests(self, **kwargs: Any) -> list[Any]:
        return []


def _resolve_runtime_sqlite_path() -> Path:
    return resolve_services_path(
        Path(SERVICES_STATE_DIRNAME) / SERVICES_STATE_DB_FILENAME
    )
