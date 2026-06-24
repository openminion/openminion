import json
import sys
from pathlib import Path
import asyncio
from typing import Any, Dict, Optional

import typer

from .constants import DEFAULT_CONFIG_FILENAME, LLM_CANDIDATE_STATUS_SUCCESS
from .runtime.client import LLMCTL, parse_call_payload
from .errors import LLMCtlError
from .orchestration import (
    AgentLLMPolicy,
    CandidateResponse,
    EnsembleResult,
    LLMOrchestrator,
    RuntimeLLMRequest,
    load_catalog_config,
    resolve_route,
)

try:
    import yaml
except ModuleNotFoundError:  # pragma: no cover - environment dependent
    yaml = None  # type: ignore[assignment]

app = typer.Typer(add_completion=False, no_args_is_help=True)
providers_app = typer.Typer(add_completion=False, no_args_is_help=True)
models_app = typer.Typer(add_completion=False, no_args_is_help=True)
agents_app = typer.Typer(add_completion=False, no_args_is_help=True)
agent_app = typer.Typer(add_completion=False, no_args_is_help=True)
orchestration_app = typer.Typer(add_completion=False, no_args_is_help=True)
ensemble_app = typer.Typer(add_completion=False, no_args_is_help=True)
DEFAULT_CONFIG_PATH = Path(DEFAULT_CONFIG_FILENAME)

app.add_typer(providers_app, name="providers")
app.add_typer(models_app, name="models")
app.add_typer(agents_app, name="agents")
app.add_typer(agent_app, name="agent")
app.add_typer(orchestration_app, name="route")
app.add_typer(ensemble_app, name="ensemble")


def _write_stdout(text: str = "") -> None:
    sys.stdout.write(f"{text}\n")


def _print_obj(obj: Dict[str, Any], json_out: bool) -> None:
    if json_out:
        _write_stdout(json.dumps(obj, indent=2, ensure_ascii=True))
        return
    if yaml is None:
        _write_stdout(json.dumps(obj, indent=2, ensure_ascii=True))
        return
    sys.stdout.write(yaml.safe_dump(obj, sort_keys=False))


def _runtime(config_path: Path) -> LLMCTL:
    return LLMCTL.from_config(config_path)


def _load_yaml(path: Path) -> Dict[str, Any]:
    if yaml is None:
        raise typer.BadParameter(
            "PyYAML is required to load YAML files; install pyyaml or use JSON"
        )
    parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(parsed, dict):
        raise typer.BadParameter("YAML file must parse to an object")
    return parsed


def _load_structured_file(path: Path) -> Dict[str, Any]:
    if path.suffix.lower() == ".json":
        return json.loads(path.read_text(encoding="utf-8"))
    return _load_yaml(path)


def _load_agent_policy(path: Path) -> AgentLLMPolicy:
    data = _load_structured_file(path)
    return AgentLLMPolicy.model_validate(data)


def _result_ok(result: Any) -> bool:
    if isinstance(result, CandidateResponse):
        return result.status == LLM_CANDIDATE_STATUS_SUCCESS
    if isinstance(result, EnsembleResult):
        selection = result.selection
        if selection is None:
            return False
        winner_id = selection.winner_candidate_id
        return any(
            candidate.candidate_id == winner_id
            and candidate.status == LLM_CANDIDATE_STATUS_SUCCESS
            for candidate in result.candidates
        )
    return False


def _cli_error(exc: LLMCtlError, json_out: bool) -> None:
    payload = {
        "error": {
            "code": exc.code,
            "message": exc.message,
            "details": exc.details,
        }
    }
    _print_obj(payload, json_out)
    raise typer.Exit(code=1)


@orchestration_app.command("resolve")
def route_resolve(
    agent_policy: Path = typer.Option(
        ..., "--agent-policy", help="Path to AgentLLMPolicy file (YAML or JSON)"
    ),
    purpose: str = typer.Option(..., "--purpose", help="Purpose to resolve"),
    json_out: bool = typer.Option(True, "--json/--no-json"),
) -> None:
    try:
        policy = _load_agent_policy(agent_policy)
        route = resolve_route(policy, purpose)
    except LLMCtlError as exc:
        _cli_error(exc, json_out)
    _print_obj({"purpose": purpose, "route": route.model_dump(mode="json")}, json_out)


@ensemble_app.command("call")
def ensemble_call(
    catalog: Path = typer.Option(
        ..., "--catalog", help="Path to catalog config (YAML or JSON)"
    ),
    agent_policy: Path = typer.Option(
        ..., "--policy", help="Path to AgentLLMPolicy file (YAML or JSON)"
    ),
    purpose: str = typer.Option(..., "--purpose"),
    request_file: Path = typer.Option(
        ..., "--request", help="Path to RuntimeLLMRequest JSON/YAML"
    ),
    config: Path = typer.Option(DEFAULT_CONFIG_PATH, "--config"),
    json_out: bool = typer.Option(True, "--json/--no-json"),
) -> None:
    try:
        llmctl = _runtime(config)
        catalog_cfg = _load_structured_file(catalog)
        orchestrator = LLMOrchestrator(llmctl, load_catalog_config(catalog_cfg))
        policy = _load_agent_policy(agent_policy)
        request_data = _load_structured_file(request_file)
        request = RuntimeLLMRequest.model_validate(request_data)
        result = asyncio.run(
            orchestrator.call_for_agent("cli", purpose, request, policy)
        )
    except LLMCtlError as exc:
        _cli_error(exc, json_out)
    payload = (
        result.model_dump(mode="json") if hasattr(result, "model_dump") else result
    )
    _print_obj(payload, json_out)
    raise typer.Exit(code=0 if _result_ok(result) else 1)


@providers_app.command("list")
def providers_list(
    config: Path = typer.Option(DEFAULT_CONFIG_PATH, "--config"),
    json_out: bool = typer.Option(True, "--json/--no-json"),
) -> None:
    llmctl = _runtime(config)
    payload = {"providers": llmctl.provider_statuses}
    _print_obj(payload, json_out)


@models_app.command("list")
def models_list(
    provider: str = typer.Option(..., "--provider"),
    config: Path = typer.Option(DEFAULT_CONFIG_PATH, "--config"),
    json_out: bool = typer.Option(True, "--json/--no-json"),
) -> None:
    llmctl = _runtime(config)
    try:
        models = llmctl.list_models(provider)
    except KeyError:
        raise typer.BadParameter(f"Unknown provider: {provider}")
    _print_obj({"provider": provider, "models": models}, json_out)


@agents_app.command("list")
def agents_list(
    config: Path = typer.Option(DEFAULT_CONFIG_PATH, "--config"),
    json_out: bool = typer.Option(True, "--json/--no-json"),
) -> None:
    llmctl = _runtime(config)
    names = sorted(llmctl.config.agents.keys())
    _print_obj({"agents": names}, json_out)


@agent_app.command("show")
def agent_show(
    agent: str = typer.Argument(...),
    config: Path = typer.Option(DEFAULT_CONFIG_PATH, "--config"),
    json_out: bool = typer.Option(True, "--json/--no-json"),
) -> None:
    llmctl = _runtime(config)
    if agent not in llmctl.config.agents:
        raise typer.BadParameter(f"Unknown agent: {agent}")
    payload = {
        "agent": agent,
        "profile": llmctl.config.agents[agent].model_dump(mode="json"),
    }
    _print_obj(payload, json_out)


@app.command()
def prompt(
    prompt_text: str = typer.Argument(...),
    agent: str = typer.Option(..., "--agent"),
    config: Path = typer.Option(DEFAULT_CONFIG_PATH, "--config"),
    json_out: bool = typer.Option(True, "--json/--no-json"),
    provider: Optional[str] = typer.Option(None, "--provider"),
    model: Optional[str] = typer.Option(None, "--model"),
    temperature: Optional[float] = typer.Option(None, "--temperature"),
    max_output_tokens: Optional[int] = typer.Option(None, "--max-output-tokens"),
) -> None:
    llmctl = _runtime(config)
    client = llmctl.client(agent)

    overrides: Dict[str, Any] = {}
    if provider is not None:
        overrides["provider"] = provider
    if model is not None:
        overrides["model"] = model
    if temperature is not None:
        overrides["temperature"] = temperature
    if max_output_tokens is not None:
        overrides["max_output_tokens"] = max_output_tokens

    response = client.complete(
        messages=[{"role": "user", "content": prompt_text}], **overrides
    )

    if json_out:
        _write_stdout(response.model_dump_json(indent=2))
        raise typer.Exit(code=0 if response.ok else 1)

    if response.ok:
        _write_stdout(response.output_text)
        raise typer.Exit(code=0)

    _write_stdout(response.error.message if response.error else "Request failed")
    raise typer.Exit(code=1)


@app.command()
def call(
    payload: Optional[str] = typer.Argument(
        None, help="JSON payload with {request, overrides}; reads stdin if omitted"
    ),
    agent: str = typer.Option(..., "--agent"),
    config: Path = typer.Option(DEFAULT_CONFIG_PATH, "--config"),
    json_out: bool = typer.Option(True, "--json/--no-json"),
) -> None:
    llmctl = _runtime(config)
    client = llmctl.client(agent)

    raw = payload if payload is not None else sys.stdin.read()
    if not raw.strip():
        raise typer.BadParameter("Empty call payload")

    try:
        request, overrides = parse_call_payload(raw)
    except LLMCtlError as exc:
        response = client._error_response(
            provider="",
            model="",
            code=exc.code,
            message=exc.message,
            details=exc.details,
        )
        _write_stdout(response.model_dump_json(indent=2))
        raise typer.Exit(code=1)

    response = client.call_sync(request=request, overrides=overrides)
    if json_out:
        _write_stdout(response.model_dump_json(indent=2))
    else:
        _write_stdout(
            response.output_text
            if response.ok
            else (response.error.message if response.error else "Request failed")
        )

    raise typer.Exit(code=0 if response.ok else 1)


@app.command()
def chat(
    agent: str = typer.Option(..., "--agent"),
    config: Path = typer.Option(DEFAULT_CONFIG_PATH, "--config"),
) -> None:
    llmctl = _runtime(config)
    client = llmctl.client(agent)
    messages = []

    while True:
        try:
            line = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            _write_stdout()
            break

        if not line:
            continue
        if line in {"/exit", "/quit"}:
            break

        messages.append({"role": "user", "content": line})
        response = client.complete(messages=messages)
        if response.ok:
            _write_stdout(f"assistant> {response.output_text}")
            messages.append({"role": "assistant", "content": response.output_text})
            continue

        _write_stdout(
            f"error> {response.error.message if response.error else 'request failed'}"
        )


def _exit_code(value: object) -> int:
    if value is None:
        return 0
    if isinstance(value, int):
        return value
    return 1


def main(argv: list[str] | None = None) -> int:
    try:
        app(args=argv, standalone_mode=False)
        return 0
    except typer.Exit as exc:
        return _exit_code(exc.exit_code)
    except SystemExit as exc:  # pragma: no cover - defensive compatibility
        return _exit_code(exc.code)


if __name__ == "__main__":
    raise SystemExit(main())
