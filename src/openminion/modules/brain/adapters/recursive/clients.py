from typing import Any

from openminion.modules.brain.interfaces import BRAIN_ADAPTER_INTERFACE_VERSION


def _safe_delegate(
    api: Any, method: str, default: Any, *args: Any, **kwargs: Any
) -> Any:
    fn = getattr(api, method, None)
    return fn(*args, **kwargs) if fn is not None else default


class RLMBridgeSessionClient:
    """Wraps SessctlAdapter to satisfy openminion_rlm.contracts.SessionClient."""

    contract_version = BRAIN_ADAPTER_INTERFACE_VERSION

    def __init__(self, session_api: Any) -> None:
        self._api = session_api
        self._store = getattr(session_api, "store", session_api)

    def get_latest_working_state(self, session_id: str) -> dict | None:
        return self._api.get_latest_working_state(session_id)

    def put_working_state(
        self, session_id: str, *, state_ref=None, state_inline=None
    ) -> int:
        return self._api.put_working_state(
            session_id, state_ref=state_ref, state_inline=state_inline
        )

    def append_event(
        self, session_id, type=None, payload=None, *, event_type=None, **kw
    ) -> str:
        return self._api.append_event(
            session_id, type or event_type or "unknown", payload or {}, **kw
        )

    def list_events(
        self,
        session_id,
        *,
        event_type=None,
        trace_id=None,
        agent_id=None,
        status=None,
        limit=None,
    ) -> list[dict]:
        if hasattr(self._store, "list_events"):
            return self._store.list_events(
                session_id,
                event_type=event_type,
                trace_id=trace_id,
                agent_id=agent_id,
                status=status,
                limit=limit,
            )
        all_events = self._api.list_events(session_id)
        if event_type:
            all_events = [e for e in all_events if e.get("type") == event_type]
        if limit:
            all_events = all_events[:limit]
        return all_events

    def get_slice(self, session_id, purpose, limits) -> dict:
        if hasattr(self._store, "get_slice"):
            return self._store.get_slice(session_id, purpose, limits)
        if hasattr(self._api, "get_slice"):
            return self._api.get_slice(session_id, purpose, limits)
        return {"messages": [], "working_memory": None}


class RLMBridgeContextClient:
    """Wraps ContextCtlAdapter to satisfy openminion_rlm.contracts.ContextClient."""

    contract_version = BRAIN_ADAPTER_INTERFACE_VERSION

    def __init__(self, context_api: Any) -> None:
        self._api = context_api

    def build_pack(self, request: dict) -> dict:
        try:
            from openminion.modules.context.schemas import BuildPackRequest

            pack_request = BuildPackRequest.model_validate(request)
            messages = self._api.build_pack(pack_request)
            return {"messages": [m.model_dump() for m in messages]}
        except Exception:
            return {"messages": []}


class RLMBridgeLLMClient:
    """Wraps LlmctlAdapter to satisfy openminion_rlm.contracts.LLMClient."""

    contract_version = BRAIN_ADAPTER_INTERFACE_VERSION

    def __init__(self, llm_api: Any) -> None:
        self._api = llm_api

    def call_for_agent(
        self,
        agent_id: str,
        purpose: str,
        request: dict,
        agent_policy: dict | None = None,
    ) -> dict:
        try:
            from openminion.modules.llm.schemas import Message, LLMRequest

            raw_messages = request.get("messages", [])
            msgs: list[Message] = []
            for m in raw_messages:
                if isinstance(m, dict):
                    msgs.append(
                        Message(
                            role=m.get("role", "user"), content=m.get("content", "")
                        )
                    )
                elif hasattr(m, "role") and hasattr(m, "content"):
                    msgs.append(Message(role=m.role, content=m.content))

            budget = (
                request.get("budget", {})
                if isinstance(request.get("budget"), dict)
                else {}
            )
            model = request.get("model") or budget.get("model") or "default"
            max_tokens = budget.get("max_tokens", 2048)

            req = LLMRequest(
                messages=msgs,
                model=model if model != "default" else None,
                max_output_tokens=max_tokens,
                metadata={"purpose": purpose, "agent_id": agent_id},
            )

            resp = self._api.client.call(req)

            usage = {}
            if hasattr(resp, "usage") and resp.usage:
                usage = {
                    "input_tokens": resp.usage.input_tokens,
                    "output_tokens": resp.usage.output_tokens,
                    "total_tokens": resp.usage.total_tokens,
                    "total_source": resp.usage.total_source
                    or ("provider" if resp.usage.total_tokens is not None else None),
                    "cached_tokens": resp.usage.cached_tokens,
                    "cache_creation_tokens": resp.usage.cache_creation_tokens,
                }

            if not resp.ok:
                error_msg = resp.error.message if resp.error else "LLM call failed"
                return {
                    "status": "error",
                    "text": error_msg,
                    "json_output": None,
                    "usage": usage,
                }

            text = resp.output_text or ""
            if not text and resp.assistant_messages:
                text = resp.assistant_messages[0].content

            json_output = None
            if resp.tool_calls:
                json_output = [
                    {"name": tc.name, "arguments": tc.arguments}
                    for tc in resp.tool_calls
                ]

            return {
                "status": "completed",
                "text": text,
                "json_output": json_output,
                "usage": usage,
            }
        except Exception as e:
            return {"status": "error", "text": str(e), "json_output": None, "usage": {}}


class RLMBridgeArtifactClient:
    """Wraps artifact adapter to satisfy openminion_rlm.contracts.ArtifactClient."""

    contract_version = BRAIN_ADAPTER_INTERFACE_VERSION

    def __init__(self, artifact_api: Any) -> None:
        self._api = artifact_api

    def ingest(
        self,
        session_id: str,
        content: str,
        mime_type: str = "text/plain",
        metadata: dict | None = None,
    ) -> str:
        return _safe_delegate(
            self._api,
            "ingest",
            "",
            session_id=session_id,
            content=content,
            mime_type=mime_type,
            metadata=metadata or {},
        )

    def get(self, artifact_id: str) -> dict | None:
        return _safe_delegate(self._api, "get", None, artifact_id)

    def list(self, session_id: str, limit: int = 50) -> list[dict]:
        return _safe_delegate(self._api, "list", [], session_id=session_id, limit=limit)


class RLMBridgeMemoryClient:
    """Wraps memory adapter to satisfy openminion_rlm.contracts.MemoryClient."""

    contract_version = BRAIN_ADAPTER_INTERFACE_VERSION

    def __init__(self, memory_api: Any) -> None:
        self._api = memory_api

    def retrieve(
        self,
        *,
        session_id: str,
        agent_id: str,
        query: str,
        k: int,
        filters: dict[str, Any] | None = None,
    ) -> list[Any]:
        if hasattr(self._api, "retrieve"):
            return self._api.retrieve(
                session_id=session_id,
                agent_id=agent_id,
                query=query,
                k=k,
                filters=filters,
            )
        if hasattr(self._api, "query_facts"):
            return self._api.query_facts(
                session_id=session_id,
                agent_id=agent_id,
                query=query,
                limit=k,
            )
        return []

    def query_facts(
        self,
        *,
        session_id: str,
        agent_id: str,
        query: str,
        limit: int,
    ) -> list[Any]:
        if hasattr(self._api, "query_facts"):
            return self._api.query_facts(
                session_id=session_id,
                agent_id=agent_id,
                query=query,
                limit=limit,
            )
        if hasattr(self._api, "retrieve"):
            return self._api.retrieve(
                session_id=session_id,
                agent_id=agent_id,
                query=query,
                k=limit,
                filters=None,
            )
        return []

    def stage_candidate(
        self,
        *,
        scope: str,
        record_type: str,
        title: str,
        content: dict[str, Any] | str,
        tags: list[str] | None = None,
        evidence_refs: list[str] | None = None,
        confidence: float | None = None,
        meta: dict[str, Any] | None = None,
    ) -> str:
        if hasattr(self._api, "stage_candidate"):
            return str(
                self._api.stage_candidate(
                    scope=scope,
                    record_type=record_type,
                    title=title,
                    content=content,
                    tags=tags,
                    evidence_refs=evidence_refs,
                    confidence=confidence,
                    meta=meta,
                )
            )
        if hasattr(self._api, "put_record"):
            return str(
                self._api.put_record(
                    scope=scope,
                    record_type=record_type,
                    title=title,
                    content=content,
                    tags=tags,
                    evidence_refs=evidence_refs,
                )
            )
        return ""


class RLMBridgeSkillClient:
    """Wraps skill adapter to satisfy openminion_rlm.contracts.SkillClient."""

    contract_version = BRAIN_ADAPTER_INTERFACE_VERSION

    def __init__(self, skill_api: Any) -> None:
        self._api = skill_api

    def search(self, query: str, limit: int = 5) -> list[dict]:
        return _safe_delegate(self._api, "search", [], query=query, limit=limit)

    def get(self, skill_id: str) -> dict | None:
        return _safe_delegate(self._api, "get", None, skill_id)


class RLMBridgeCompressClient:
    """Wraps compress adapter to satisfy openminion_rlm.contracts.CompressionClient."""

    contract_version = BRAIN_ADAPTER_INTERFACE_VERSION

    def __init__(self, compress_api: Any) -> None:
        self._api = compress_api

    def compress(self, text: str, max_tokens: int = 2000) -> str:
        return _safe_delegate(
            self._api, "compress", text, text=text, max_tokens=max_tokens
        )

    def extract(self, text: str, num_blocks: int = 5) -> list[str]:
        return _safe_delegate(
            self._api, "extract", [text], text=text, num_blocks=num_blocks
        )
