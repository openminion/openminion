import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Optional, cast

from openminion.services.config import (
    resolve_services_env,
    resolve_services_path,
)
from openminion.base.constants import OPENMINION_IDENTITY_DB_ENV
from openminion.services.bootstrap.paths import (
    SERVICES_IDENTITY_DB_FILENAME,
    SERVICES_IDENTITY_SUBDIR,
    SERVICES_STATE_DB_FILENAME,
    SERVICES_STATE_DIRNAME,
)
from openminion.services.context.constants import (
    CONTEXTCTL_DUAL_RENDER_ENV,
    OPENMINION_SESSION_CONTEXT_TOKEN_BUDGET_ENV,
)
from openminion.modules.context.pack.semantics import resolve_context_total_token_budget

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
        agent_id: str = "",
        runtime_token_budget: int = 0,
        session_client: Any | None = None,
        memory_client: Any | None = None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self._enabled = True
        self._dual_render = contextctl_dual_render
        self._agent_id = agent_id
        self._runtime_token_budget = max(0, int(runtime_token_budget))
        self._session_client = session_client
        # accept an injected memory client so the adapter does
        self._memory_client = memory_client
        self._log = logger or _logger
        if self._dual_render:
            self._log.warning(
                "%s=true but ContextCtlGatewayAdapter is not wired into gateway "
                "build_turn_context(); dual-render parity does not execute until "
                "P3b gateway call-site integration is completed.",
                CONTEXTCTL_DUAL_RENDER_ENV,
            )

    @classmethod
    def from_env(
        cls,
        *,
        agent_id: str = "",
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
            contextctl_dual_render=env_config.get_bool(
                CONTEXTCTL_DUAL_RENDER_ENV, False
            ),
            agent_id=agent_id,
            runtime_token_budget=_int_env(OPENMINION_SESSION_CONTEXT_TOKEN_BUDGET_ENV),
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
        history: List[object],
        session_id: str,
        agent_id: str,
        query: str,
        contextctl_messages: Optional[List[ContextCtlMessage]] = None,
    ) -> List[object]:
        """C-09: Select which history to pass to the agent."""
        if self._dual_render and contextctl_messages is not None:
            # log parity diff for monitoring
            self._log_parity(
                history_count=len(history),
                contextctl_count=len(contextctl_messages),
                session_id=session_id,
                agent_id=agent_id,
            )

        if contextctl_messages is not None:
            # contextctl is default path

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
    ) -> Optional[List[ContextCtlMessage]]:
        """C-08: Attempt to build context pack via ctxctl."""
        try:
            messages = self._call_ctxctl(
                session_id=session_id,
                agent_id=agent_id,
                query=query,
                purpose=purpose,
            )
            return messages
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
    ) -> List[ContextCtlMessage]:
        """Build a context pack through the ContextCtl service."""
        from openminion.modules.context.schemas import (
            BuildPackRequest,
            Purpose,
            default_budgets_for,
        )
        from openminion.modules.context.contracts import IdentityClient
        from openminion.modules.context.service import ContextCtlService

        try:
            from openminion.modules.identity.storage.store import SQLiteIdentityStore
            from openminion.modules.identity.runtime.service import IdentityCtl
            from openminion.services.identity.bootstrap import ensure_default_profile

            db_path = resolve_services_env().get(OPENMINION_IDENTITY_DB_ENV, "").strip()

            if not db_path:
                db_path = str(
                    resolve_services_path(
                        Path(SERVICES_IDENTITY_SUBDIR) / SERVICES_IDENTITY_DB_FILENAME
                    )
                )

            _store = SQLiteIdentityStore(sqlite_path=db_path)
            identity_ctl: Any = IdentityCtl(store=_store)

            # Ensure default profile exists
            ensure_default_profile(identity_ctl, agent_id, "")

        except ImportError:
            # Fall back to _EchoIdentityClient if openminion_identity not installed
            identity_ctl = _EchoIdentityClient(agent_id=agent_id)
        identity_client = cast(IdentityClient, identity_ctl)
        session_client = self._session_client or _RuntimeMappedSessionClient(
            sqlite_path=_resolve_runtime_sqlite_path()
        )
        # prefer an injected memory_client over the NullMemoryClient
        memory_stub = self._memory_client or _NullMemoryClient()
        artifact_stub = _NullArtifactClient()

        service = ContextCtlService(
            identityctl=identity_client,
            sessctl=session_client,
            memctl=memory_stub,
            artifactctl=artifact_stub,
        )
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

    def _contextctl_to_history(self, messages: List[ContextCtlMessage]) -> List[object]:
        from openminion.base.types import Message

        result: list[Message] = []
        for msg in messages:
            result.append(
                Message(
                    channel="contextctl",
                    target="",
                    body=msg.content,
                    metadata={"role": msg.role, "source": "contextctl"},
                )
            )
        return result  # type: ignore[return-value]

    def _log_parity(
        self,
        *,
        history_count: int,
        contextctl_count: int,
        session_id: str,
        agent_id: str,
    ) -> None:
        """C-08: Emit parity log for dual-render comparison monitoring."""
        self._log.info(
            "context_adapter dual_render: "
            "session_id=%s agent_id=%s history_count=%d contextctl_count=%d delta=%d",
            session_id,
            agent_id,
            history_count,
            contextctl_count,
            abs(history_count - contextctl_count),
        )


# Session mapping + fallback stubs


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


class _RuntimeMappedSessionClient:
    """Mapping-backed SessionClient sourced from runtime session storage."""

    contract_version = "v1"

    def __init__(self, sqlite_path: Path) -> None:
        self._sqlite_path = sqlite_path
        self._migrated = False
        self._connection: sqlite3.Connection | None = None
        self._store: Any | None = None

    def _ensure_ready(self) -> Any:
        """Ensure migration has run and the connection/store are open.

        Idempotent. Migration runs once per instance. The connection and
        SessionStore are reused across `get_slice` calls.
        """
        from openminion.modules.storage.runtime.migrations import migrate_database
        from openminion.modules.storage.runtime.session_store import SessionStore
        from openminion.modules.storage.runtime.sqlite import connect_database

        if not self._migrated:
            migrate_database(self._sqlite_path)
            self._migrated = True
        if self._connection is None:
            self._connection = connect_database(self._sqlite_path)
            self._store = SessionStore(self._connection)
        return self._store

    def get_slice(
        self, *, session_id: str, purpose: str, limits: dict[str, int]
    ) -> Any:
        from openminion.services.context.slices import (
            build_session_slice_from_runtime_store,
        )

        del purpose
        store = self._ensure_ready()
        return build_session_slice_from_runtime_store(
            store=store,
            session_id=session_id,
            limits=limits,
            slice_version="runtime-map:v1",
        )

    def close(self) -> None:
        """Close the cached connection if open. Idempotent."""
        if self._connection is not None:
            try:
                self._connection.close()
            finally:
                self._connection = None
                self._store = None

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass


class _NullMemoryClient:
    contract_version = "v1"

    def query_facts(self, **kwargs: Any) -> list[Any]:
        return []

    def query_memory_cards(self, **kwargs: Any) -> list[Any]:
        return []

    def recall_session_start_memory(self, **kwargs: Any) -> list[Any]:
        return []

    def recall_mid_session_memory(self, **kwargs: Any) -> list[Any]:
        return []

    def recall_recent_session_artifacts(self, **kwargs: Any) -> list[Any]:
        return []

    def get_procedure(self, **kwargs: Any) -> None:
        return None


class _NullArtifactClient:
    contract_version = "v1"

    def query_digests(self, **kwargs: Any) -> list[Any]:
        return []


def _resolve_runtime_sqlite_path() -> Path:
    return resolve_services_path(
        Path(SERVICES_STATE_DIRNAME) / SERVICES_STATE_DB_FILENAME
    )
