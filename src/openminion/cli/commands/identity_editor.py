from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any

import yaml

from openminion.base.config.runtime.identity import resolve_identity_root_from_env
from openminion.cli.config import (
    load_cli_config,
    resolve_cli_identity_db_path,
    resolve_cli_roots,
    resolve_identity_bundle_root,
)
from openminion.modules.identity import (
    IdentityCtl,
    IdentityOperatorError,
    InMemoryIdentityStore,
    SQLiteIdentityStore,
    apply_identity_candidate,
    inspect_identity,
    validate_identity_candidate,
)


def run_identity_inspect(
    agent_id: str | None = None,
    *,
    json_output: bool = False,
    config_path: object | None = None,
    home_root: str | Path | None = None,
    data_root: str | Path | None = None,
) -> None:
    ctl, identity_root = get_identity_context(
        config_path=config_path,
        home_root=home_root,
        data_root=data_root,
    )
    try:
        payload = inspect_identity(
            ctl,
            identity_root=identity_root,
            agent_id=(str(agent_id or "").strip() or None),
        )
    except IdentityOperatorError as exc:
        _exit_operator_error(exc, json_output=json_output)
    print_structured_payload(payload, json_output=json_output)


def run_identity_candidate_validate(
    file_path: str,
    *,
    strict: bool = False,
    json_output: bool = False,
) -> None:
    ctl = IdentityCtl(store=InMemoryIdentityStore())
    try:
        payload = validate_identity_candidate(
            ctl,
            candidate_path=Path(file_path),
            strict=strict,
        )
    except IdentityOperatorError as exc:
        _exit_operator_error(exc, json_output=json_output)
    print_structured_payload(payload, json_output=json_output)


def run_identity_apply(
    file_path: str,
    *,
    expected_profile_version: str,
    expected_source_sha256: str,
    strict: bool = False,
    json_output: bool = False,
    config_path: object | None = None,
    home_root: str | Path | None = None,
    data_root: str | Path | None = None,
) -> None:
    ctl, identity_root = get_identity_context(
        config_path=config_path,
        home_root=home_root,
        data_root=data_root,
    )
    try:
        payload = apply_identity_candidate(
            ctl,
            identity_root=identity_root,
            candidate_path=Path(file_path),
            expected_profile_version=expected_profile_version,
            expected_source_sha256=expected_source_sha256,
            strict=strict,
        )
    except IdentityOperatorError as exc:
        _exit_operator_error(exc, json_output=json_output)
    print_structured_payload(payload, json_output=json_output)


def print_structured_payload(payload: dict[str, Any], *, json_output: bool) -> None:
    if json_output:
        print(json.dumps(payload, sort_keys=True))
        return
    print(yaml.safe_dump(payload, sort_keys=False))


def get_identity_context(
    *,
    config_path: object | None = None,
    home_root: str | Path | None = None,
    data_root: str | Path | None = None,
) -> tuple[IdentityCtl, Path]:
    config = load_cli_config(
        config_path,
        home_root=home_root,
        data_root=data_root,
    )
    roots = resolve_cli_roots(
        config_path=config_path,
        home_root=home_root,
        data_root=data_root,
    )
    db_path = resolve_cli_identity_db_path(config, roots=roots).expanduser()
    identity_root = resolve_identity_root_from_env(
        env=roots.env,
        home_root=roots.home_root,
        data_root=roots.data_root,
        configured_root=resolve_identity_bundle_root(config),
    )
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return (
        IdentityCtl(store=SQLiteIdentityStore(sqlite_path=str(db_path))),
        identity_root,
    )


def _exit_operator_error(exc: IdentityOperatorError, *, json_output: bool) -> None:
    payload = {"ok": False, "code": exc.code, "message": str(exc), "issues": exc.issues}
    if json_output:
        print(json.dumps(payload, sort_keys=True), file=sys.stderr)
    else:
        print(f"ERROR: {exc}", file=sys.stderr)
        for issue in exc.issues:
            print(f"ERROR: {issue}", file=sys.stderr)
    raise SystemExit(1)


__all__ = [
    "get_identity_context",
    "print_structured_payload",
    "run_identity_apply",
    "run_identity_candidate_validate",
    "run_identity_inspect",
]
