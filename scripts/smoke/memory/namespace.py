"""Run deterministic typed-memory namespace CLI and HTTP smoke checks."""

from __future__ import annotations

import argparse
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from threading import Thread
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from openminion.api.server.app import _OpenMinionAPIHandler
from openminion.modules.memory.models import MemoryNamespace, MemoryRecord
from openminion.modules.memory.service import MemoryService
from openminion.modules.memory.storage.sqlite.store import SQLiteMemoryStore
from openminion.services.agent.memory.gateway_adapter import (
    DisabledMemoryGatewayAdapter,
    MemoryServiceGatewayAdapter,
)


def _record(
    record_id: str,
    *,
    scope: str,
    namespace: MemoryNamespace | None,
) -> MemoryRecord:
    return MemoryRecord(
        id=record_id,
        scope=scope,
        type="fact",
        title="Shared convention",
        content="shared deployment convention",
        namespace=namespace,
        created_at="2026-07-10T00:00:00Z",
        updated_at="2026-07-10T00:00:00Z",
    )


def _seed_store(db_path: Path) -> MemoryService:
    store = SQLiteMemoryStore(db_path, artifactctl=None)
    store.put(
        _record(
            "typed-a",
            scope="agent:agent-a",
            namespace=MemoryNamespace(
                user_id="user-a",
                agent_id="agent-a",
                project_id="project-a",
            ),
        )
    )
    store.put(
        _record(
            "typed-b",
            scope="agent:agent-b",
            namespace=MemoryNamespace(
                user_id="user-b",
                agent_id="agent-b",
                project_id="project-b",
            ),
        )
    )
    store.put(_record("legacy", scope="session:legacy", namespace=None))
    return MemoryService(store)


def _run_memctl(db_path: Path, *args: str) -> list[dict]:
    executable = Path(sys.executable).with_name("memctl")
    completed = subprocess.run(
        [str(executable), *args, "--json", "--db", str(db_path)],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)
    if not isinstance(payload, list):
        raise AssertionError("memctl did not return a JSON record list")
    return payload


def _post(base_url: str, path: str, body: dict) -> tuple[int, dict]:
    request = Request(
        base_url + path,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=5) as response:  # noqa: S310 - loopback smoke
            return response.status, json.loads(response.read())
    except HTTPError as exc:
        return exc.code, json.loads(exc.read())


def _post_exchange(
    base_url: str,
    exchanges: dict[str, dict],
    key: str,
    body: dict,
    path: str = "/memory/records/list",
) -> tuple[int, dict]:
    status, payload = _post(base_url, path, body)
    exchanges[key] = {"request": body, "status": status, "response": payload}
    return status, payload


def _assert_ids(records: list[dict], expected: list[str]) -> None:
    actual = [str(record.get("id", "")) for record in records]
    if actual != expected:
        raise AssertionError(f"expected record ids {expected}, got {actual}")


def _write_json(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def run_smoke(artifact_dir: Path) -> dict:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    cli_results: dict[str, list[dict]] = {}
    exchanges: dict[str, dict] = {}
    cases: dict[str, str] = {}

    with tempfile.TemporaryDirectory(dir=artifact_dir) as temp_dir:
        db_path = Path(temp_dir) / "memory.db"
        service = _seed_store(db_path)

        cli_results["list"] = _run_memctl(
            db_path,
            "list",
            "--user-id",
            "user-a",
            "--agent-id",
            "agent-a",
        )
        _assert_ids(cli_results["list"], ["typed-a"])
        cases["cli_typed_list"] = "pass"

        cli_results["search"] = _run_memctl(
            db_path,
            "search",
            "deployment",
            "--user-id",
            "user-a",
        )
        _assert_ids(cli_results["search"], ["typed-a"])
        cases["cli_typed_search"] = "pass"

        cli_results["legacy"] = _run_memctl(
            db_path,
            "list",
            "--scope",
            "session:legacy",
        )
        _assert_ids(cli_results["legacy"], ["legacy"])
        cases["cli_legacy_scope"] = "pass"

        for field, value in (
            ("user-id", "missing-user"),
            ("agent-id", "missing-agent"),
            ("project-id", "missing-project"),
        ):
            key = f"wrong_{field.replace('-', '_')}"
            cli_results[key] = _run_memctl(db_path, "list", f"--{field}", value)
            _assert_ids(cli_results[key], [])
        cases["cli_mismatches"] = "pass"

        adapter = MemoryServiceGatewayAdapter.__new__(MemoryServiceGatewayAdapter)
        adapter._service = service

        class Handler(_OpenMinionAPIHandler):
            runtime = SimpleNamespace(memory_queries=adapter)
            config_path = None
            runtime_bootstrap_error = None

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        host, port = server.server_address
        base_url = f"http://{host}:{port}"
        try:
            list_body = {
                "namespace": {"user_id": "user-a", "agent_id": "agent-a"}
            }
            status, payload = _post_exchange(base_url, exchanges, "list", list_body)
            if status != 200:
                raise AssertionError(f"API list returned {status}")
            _assert_ids(payload["records"], ["typed-a"])
            cases["api_typed_list"] = "pass"

            search_body = {
                "query": "deployment",
                "namespace": {"project_id": "project-a"},
            }
            status, payload = _post_exchange(
                base_url,
                exchanges,
                "search",
                search_body,
                path="/memory/records/search",
            )
            if status != 200:
                raise AssertionError(f"API search returned {status}")
            _assert_ids(payload["records"], ["typed-a"])
            cases["api_typed_search"] = "pass"

            for field in ("user_id", "agent_id", "project_id"):
                body = {"namespace": {field: f"missing-{field}"}}
                status, payload = _post_exchange(
                    base_url,
                    exchanges,
                    f"wrong_{field}",
                    body,
                )
                if status != 200 or payload.get("count") != 0:
                    raise AssertionError(f"API mismatch did not return zero for {field}")
            cases["api_mismatches"] = "pass"

            conflict_body = {
                "scope": "agent:agent-a",
                "namespace": {"agent_id": "agent-b"},
            }
            status, payload = _post_exchange(
                base_url, exchanges, "conflict", conflict_body
            )
            if status != 400 or payload.get("error", {}).get("code") != "invalid_request":
                raise AssertionError("scope conflict did not return 400 invalid_request")
            cases["api_scope_conflict"] = "pass"

            Handler.runtime = SimpleNamespace(
                memory_queries=DisabledMemoryGatewayAdapter(agent_id="agent-a")
            )
            disabled_body = {"namespace": {"agent_id": "agent-a"}}
            status, payload = _post_exchange(
                base_url, exchanges, "disabled", disabled_body
            )
            if status != 503 or payload.get("error", {}).get("code") != "memory_unavailable":
                raise AssertionError("disabled memory did not return 503 memory_unavailable")
            cases["api_disabled"] = "pass"
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    _write_json(artifact_dir / "cli-results.json", cli_results)
    _write_json(artifact_dir / "api-exchanges.json", exchanges)
    summary = {
        "ok": all(result == "pass" for result in cases.values()),
        "provider_free": True,
        "external_network": False,
        "cases": cases,
    }
    _write_json(artifact_dir / "summary.json", summary)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-dir", type=Path, required=True)
    args = parser.parse_args()
    summary = run_smoke(args.artifact_dir.expanduser().resolve(strict=False))
    print(json.dumps(summary, sort_keys=True))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
