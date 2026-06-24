import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from openminion.modules.cli_common import (
    add_common_module_root_args,
    apply_home_data_root_env,
    print_json_payload,
)
from openminion.modules.config import (
    is_module_standalone_mode,
    resolve_module_data_root,
    resolve_module_home_root,
)
from .constants import (
    DEFAULT_INTEGRATED_SESSION_DB_SUBPATH,
    DEFAULT_STANDALONE_SESSION_DB_SUBPATH,
)
from .schemas import MetaDirective, RLMConstraints, RetrievalFilters, TaskState
from .service import RLMService


class _EchoCtxClient:
    def build_pack(self, request: Any) -> dict[str, Any]:
        query = ""
        if isinstance(request, dict):
            query = str(request.get("query", ""))
        else:
            query = str(getattr(request, "query", ""))
        return {
            "pack_version": "echo-pack-v1",
            "messages": [
                {
                    "role": "system",
                    "content": "You are a local recursive assistant. Prefer concise, evidence-grounded outputs.",
                },
                {"role": "user", "content": query},
            ],
        }


class _EchoLLMClient:
    def call_for_agent(
        self,
        agent_id: str,
        purpose: str,
        request: dict[str, Any],
        agent_policy: dict[str, Any],
    ) -> dict[str, Any]:
        del agent_id, purpose, agent_policy
        messages = request.get("messages", []) if isinstance(request, dict) else []
        prompt = ""
        if messages:
            prompt = str(messages[-1].get("content", ""))
        payload = {
            "final": True,
            "answer": f"[echo] {prompt}",
            "next_query": None,
            "episode_note": "Echo fallback generated a final response.",
            "evidence_refs": [],
            "citations": [],
            "wm_update": {},
            "memory_write_intents": [],
        }
        return {
            "status": "success",
            "text": json.dumps(payload),
            "json_output": payload,
        }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rlmctl", description="openminion-rlm local-first CLI"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    add_common_module_root_args(common)
    common.add_argument("--sessctl-db", default=None)
    common.add_argument("--session-id", required=True)
    common.add_argument("--agent-id", default="router-agent")

    refresh = sub.add_parser(
        "refresh-wm",
        parents=[common],
        help="Refresh working memory from recent session state",
    )
    refresh.add_argument("--reason", default="manual_refresh")

    retrieve = sub.add_parser(
        "retrieve", parents=[common], help="Retrieve candidate EM/SM snippets"
    )
    retrieve.add_argument("--query", required=True)
    retrieve.add_argument("--k", type=int, default=8)
    retrieve.add_argument(
        "--strategy",
        default="auto",
        choices=["auto", "contextual", "raptor", "longrag_doc_group"],
    )
    retrieve.add_argument(
        "--sources",
        default="sm,em,skill",
        help="Comma separated source list from sm,em,skill",
    )

    generate = sub.add_parser(
        "generate", parents=[common], help="Run recursive generation with echo LLM stub"
    )
    generate.add_argument("--query", required=True)
    generate.add_argument(
        "--constraints-json",
        default="{}",
        help="JSON object for RLM constraints",
    )
    generate.add_argument(
        "--task-state-json",
        default="{}",
        help="JSON object for task state",
    )
    generate.add_argument(
        "--meta-directive-json",
        default="{}",
        help="JSON object for meta directive",
    )

    expand = sub.add_parser(
        "expand", parents=[common], help="Expand a retrieval reference"
    )
    expand.add_argument("--ref", required=True)
    expand.add_argument("--mode", default="window")
    expand.add_argument("--k", type=int, default=5)

    return parser


def _load_sessctl(db_path: str) -> Any:
    try:
        from openminion.modules.session.storage.sqlite_store import SQLiteSessionStore
    except ModuleNotFoundError as exc:  # pragma: no cover - environment dependent
        raise RuntimeError(
            "openminion_session is required in PYTHONPATH for CLI usage"
        ) from exc
    return SQLiteSessionStore(Path(db_path).expanduser().resolve())


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    home_root = str(getattr(args, "home_root", "") or "").strip()
    data_root = str(getattr(args, "data_root", "") or "").strip()
    apply_home_data_root_env(home_root=home_root, data_root=data_root)

    sessctl_db_raw = str(getattr(args, "sessctl_db", "") or "").strip()
    if not sessctl_db_raw:
        env_map = os.environ
        standalone_mode = is_module_standalone_mode(env_map)
        if standalone_mode:
            sessctl_db_raw = str(
                (Path.home() / DEFAULT_STANDALONE_SESSION_DB_SUBPATH).resolve()
            )
        else:
            resolved_home_root = resolve_module_home_root(
                None,
                env_map,
                fallback_to_cwd=True,
            )
            resolved_data_root = resolve_module_data_root(
                home_root=resolved_home_root,
                env=env_map,
            )
            sessctl_db_raw = str(
                (resolved_data_root / DEFAULT_INTEGRATED_SESSION_DB_SUBPATH).resolve()
            )

    sessctl = _load_sessctl(sessctl_db_raw)
    service = RLMService(
        sessctl=sessctl, contextctl=_EchoCtxClient(), llmctl=_EchoLLMClient()
    )

    try:
        if args.command == "refresh-wm":
            wm = service.refresh_working_memory(
                args.session_id, args.agent_id, args.reason
            )
            print_json_payload(wm.model_dump(mode="json"))
            return 0

        if args.command == "retrieve":
            sources = [
                item.strip() for item in str(args.sources).split(",") if item.strip()
            ]
            payload = service.retrieve(
                session_id=args.session_id,
                agent_id=args.agent_id,
                query=args.query,
                k=args.k,
                strategy=args.strategy,
                filters=RetrievalFilters(
                    include_sources=sources, strategy=args.strategy
                ),
            )
            print_json_payload([item.model_dump(mode="json") for item in payload])
            return 0

        if args.command == "generate":
            try:
                constraints_raw = json.loads(args.constraints_json)
                task_state_raw = json.loads(args.task_state_json)
                meta_raw = json.loads(args.meta_directive_json)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSON argument: {exc}") from exc
            response = service.generate(
                session_id=args.session_id,
                agent_id=args.agent_id,
                purpose="act",
                query=args.query,
                constraints=RLMConstraints.model_validate(constraints_raw),
                ts=TaskState.model_validate(task_state_raw),
                meta_directive=MetaDirective.model_validate(meta_raw),
            )
            print_json_payload(response.model_dump(mode="json"))
            return 0

        if args.command == "expand":
            payload = service.expand(ref=args.ref, mode=args.mode, k=args.k)
            print_json_payload([item.model_dump(mode="json") for item in payload])
            return 0

        parser.error(f"unsupported command: {args.command}")
        return 2
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        close = getattr(sessctl, "close", None)
        if callable(close):
            close()


if __name__ == "__main__":
    raise SystemExit(main())
