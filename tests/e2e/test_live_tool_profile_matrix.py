from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path

import pytest

from openminion.api.runtime import APIRuntime
from openminion.api.turns import run_turn
from openminion.base.config import configured_agent_ids, load_config
from tests.helpers.live_e2e_profiles import (
    LiveAgentProfile as _LiveAgent,
    agents_from_bundle as _agents_from_bundle,
    parse_live_agent_targets_env,
    resolve_live_config_path,
)

pytestmark = [pytest.mark.e2e, pytest.mark.timeout(600)]

_RAW_TOOL_MARKUP_RE = re.compile(
    r"<minimax:tool_call>|<functioncall>|<invoke\s+name=|\[tool_call\]",
    re.IGNORECASE,
)
_SEARCH_SOURCE_MARKER_RE = re.compile(
    r"(source=|via\s+[a-z0-9_.-]+)",
    re.IGNORECASE,
)
_OPENMINION_ROOT = Path(__file__).resolve().parents[2]
_OPENMINION_ROOT_STR = str(_OPENMINION_ROOT)
_README_PATH_STR = str(_OPENMINION_ROOT / "README.md")


def _openminion_root() -> Path:
    return _OPENMINION_ROOT


def _agent_framework_root() -> Path:
    return _openminion_root().parent


@dataclass(frozen=True)
class _Scenario:
    id: str
    message: str
    expected_tools: tuple[str, ...]
    forced_tools: tuple[str, ...]
    require_source_tag: bool = False
    forbidden_body_tokens: tuple[str, ...] = ()


@dataclass(frozen=True)
class _ParityScenario:
    id: str
    message: str
    expected_tools: tuple[str, ...]
    forced_tools: tuple[str, ...] = ()
    require_source_tag: bool = False


_PER_AGENT_PROFILES: tuple[_LiveAgent, ...] = (
    _LiveAgent("openrouter-minimax", Path("per-agent-openrouter-minimax.json")),
    _LiveAgent(
        "openrouter-claude-haiku", Path("per-agent-openrouter-claude-haiku.json")
    ),
)

_DEFAULT_AGENT_PROFILES: tuple[_LiveAgent, ...] = (
    *_PER_AGENT_PROFILES,
    *_agents_from_bundle("agents-alibaba.json", framework_root=_agent_framework_root()),
    *_agents_from_bundle(
        "agents-openrouter.json", framework_root=_agent_framework_root()
    ),
)
_ENV_AGENT_PROFILES: tuple[_LiveAgent, ...] = parse_live_agent_targets_env(
    "OPENMINION_LIVE_TOOL_E2E_TARGETS",
    framework_root=_agent_framework_root(),
)
_AGENT_PROFILES: tuple[_LiveAgent, ...] = _ENV_AGENT_PROFILES or _DEFAULT_AGENT_PROFILES

_OPENAI_PARITY_PROFILE = _LiveAgent(
    "openrouter-gpt-5.4", Path("per-agent-openrouter-gpt-5-4.json")
)

_SCENARIOS: tuple[_Scenario, ...] = (
    _Scenario(
        id="search_news",
        message='tool web.search {"query":"latest news on iran"}',
        expected_tools=("web.search",),
        forced_tools=("web.search",),
        require_source_tag=True,
    ),
    _Scenario(
        id="weather_now",
        message='tool weather {"location":"san francisco"}',
        expected_tools=("weather",),
        forced_tools=("weather",),
    ),
    _Scenario(
        id="file_list",
        message=f'tool file.list_dir {{"path":"{_OPENMINION_ROOT_STR}"}}',
        expected_tools=("file.list_dir",),
        forced_tools=("file.list_dir",),
    ),
    _Scenario(
        id="file_read",
        message=f'tool file.read {{"path":"{_README_PATH_STR}","max_chars":120}}',
        expected_tools=("file.read",),
        forced_tools=("file.read",),
        forbidden_body_tokens=("Denied by policy",),
    ),
)

_PARITY_SCENARIOS: tuple[_ParityScenario, ...] = (
    _ParityScenario(
        id="nl_time",
        message="what time is it in UTC right now?",
        expected_tools=("time",),
    ),
    _ParityScenario(
        id="nl_weather",
        message="what is the weather in San Francisco right now?",
        expected_tools=("weather",),
    ),
    _ParityScenario(
        id="nl_search",
        message="check latest news on iran and summarize briefly",
        expected_tools=("web.search",),
        require_source_tag=True,
    ),
    _ParityScenario(
        id="explicit_time",
        message="tool time {}",
        expected_tools=("time",),
    ),
    _ParityScenario(
        id="explicit_weather",
        message='tool weather {"location":"san francisco"}',
        expected_tools=("weather",),
    ),
    _ParityScenario(
        id="explicit_search",
        message='tool web.search {"query":"latest news on iran"}',
        expected_tools=("web.search",),
        require_source_tag=True,
    ),
)


def _resolve_agent_id(config_path: Path, framework_root: Path) -> str:
    config = load_config(str(config_path), home_root=framework_root)
    preferred = str(config.agents[next(iter(config.agents.keys()))].name or "").strip()
    configured = configured_agent_ids(config)
    if preferred and preferred in configured:
        return preferred
    if configured:
        return configured[0]
    raise AssertionError(f"no configured agent ids found in {config_path}")


def _using_env_profile_overrides() -> bool:
    return bool(_ENV_AGENT_PROFILES)


def _require_live_flag() -> None:
    if str(os.getenv("OPENMINION_LIVE_TOOL_E2E", "")).strip() != "1":
        pytest.skip(
            "OPENMINION_LIVE_TOOL_E2E=1 not set; skipping live tool matrix E2E."
        )


def _timeout_seconds() -> int:
    raw = str(os.getenv("OPENMINION_LIVE_TOOL_E2E_TIMEOUT", "120")).strip() or "120"
    try:
        value = int(raw)
    except ValueError:
        return 120
    return max(value, 30)


def _is_quota_error(exc: Exception) -> bool:
    token = str(exc or "").lower()
    return (
        "http 402" in token
        or "requires more credits" in token
        or "insufficient credits" in token
    )


def _is_infra_unavailable_response(
    body: str, metadata: dict, tool_results: list[dict]
) -> bool:
    token = " ".join(
        [
            str(body or ""),
            str(metadata.get("error", "") or ""),
            str(metadata.get("tool_results", "") or ""),
        ]
    ).lower()
    if any(
        marker in token
        for marker in (
            "http 402",
            "requires more credits",
            "insufficient credits",
            "can't assign requested address",
            "no endpoints found",
            "deprecated",
            "switching to",
            "switch models",
        )
    ):
        return True
    for item in tool_results:
        item_blob = json.dumps(item, sort_keys=True).lower()
        if "can't assign requested address" in item_blob:
            return True
        if "requires more credits" in item_blob or '"code": 402' in item_blob:
            return True
        if "no endpoints found" in item_blob or "deprecated" in item_blob:
            return True
    return False


def _is_auth_failure_response(
    body: str, metadata: dict, tool_results: list[dict]
) -> bool:
    token = " ".join(
        [
            str(body or ""),
            str(metadata.get("error", "") or ""),
            str(metadata.get("provider_error", "") or ""),
            str(metadata.get("tool_results", "") or ""),
        ]
    ).lower()
    if any(
        marker in token
        for marker in (
            "invalid access token",
            "token expired",
            "rejected authentication",
            "authentication failed",
            "unauthorized",
        )
    ):
        return True
    for item in tool_results:
        item_blob = json.dumps(item, sort_keys=True).lower()
        if any(
            marker in item_blob
            for marker in (
                "invalid access token",
                "token expired",
                "rejected authentication",
                "authentication failed",
                "unauthorized",
            )
        ):
            return True
    return False


def _has_policy_denial(body: str, tool_results: list[dict]) -> bool:
    if "denied by policy" in body.lower():
        return True
    for item in tool_results:
        error_code = str(item.get("error_code", "")).strip().upper()
        if error_code in {"POLICY_DENIED", "POLICY_BLOCKED"}:
            return True
        error = item.get("error")
        if isinstance(error, dict):
            nested = str(error.get("code", "")).strip().upper()
            if nested in {"POLICY_DENIED", "POLICY_BLOCKED"}:
                return True
    return False


def _tool_results_from_metadata(metadata: dict) -> list[dict]:
    raw = metadata.get("tool_results", [])
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    if isinstance(raw, dict):
        return [raw]
    if isinstance(raw, str):
        token = raw.strip()
        if not token:
            return []
        try:
            parsed = json.loads(token)
        except json.JSONDecodeError:
            return []
        if isinstance(parsed, dict):
            return [parsed]
        if isinstance(parsed, list):
            return [item for item in parsed if isinstance(item, dict)]
    return []


def _run_live_turn_with_retries(
    *,
    agent_id: str,
    config_path: Path,
    runtime: APIRuntime,
    scenario_id: str,
    message: str,
    target: str,
    forced_tools: tuple[str, ...] = (),
) -> tuple[dict, dict, list[dict], str]:
    payload: dict | None = None
    metadata: dict = {}
    tool_results: list[dict] = []
    last_error: Exception | None = None

    attempts = 3
    for attempt in range(1, attempts + 1):
        session_id = f"{target}-{agent_id}-{scenario_id}-{uuid.uuid4().hex[:8]}"
        try:
            payload = run_turn(
                str(config_path),
                {
                    "message": message,
                    "agent_id": agent_id,
                    "channel": "console",
                    "target": target,
                    "session_id": session_id,
                    "forced_tools": list(forced_tools),
                    "timeout_seconds": _timeout_seconds(),
                },
                runtime=runtime,
            )
        except Exception as exc:  # noqa: BLE001 - live E2E retries transient provider/runtime failures
            if _is_quota_error(exc):
                pytest.skip(
                    f"provider quota exhausted for agent={agent_id} scenario={scenario_id}: {exc}"
                )
            last_error = exc
            if attempt < attempts:
                continue
            raise AssertionError(
                f"run_turn failed for agent={agent_id} scenario={scenario_id} after {attempts} attempts: {type(exc).__name__}: {exc}"
            ) from exc

        metadata = (
            payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        )
        tool_results = _tool_results_from_metadata(metadata)
        body = str(payload.get("body", "") or "")

        if _has_policy_denial(body, tool_results):
            pytest.skip(
                f"policy denied forced tool for agent={agent_id} scenario={scenario_id}: {body}"
            )
        if _is_auth_failure_response(body, metadata, tool_results):
            pytest.skip(
                f"provider credentials invalid for agent={agent_id} scenario={scenario_id}: {body}"
            )
        if _is_infra_unavailable_response(body, metadata, tool_results):
            pytest.skip(
                f"provider/network unavailable for agent={agent_id} scenario={scenario_id}: {body}"
            )

        if tool_results or attempt == attempts:
            break

    assert payload is not None, (
        f"missing payload for agent={agent_id} scenario={scenario_id}; "
        f"last_error={type(last_error).__name__ if last_error else 'none'}"
    )
    body = str(payload.get("body", "") or "")
    return payload, metadata, tool_results, body


@pytest.fixture(
    scope="session", params=_AGENT_PROFILES, ids=[p.profile_id for p in _AGENT_PROFILES]
)
def live_agent(request: pytest.FixtureRequest) -> dict:
    _require_live_flag()

    profile = request.param
    assert isinstance(profile, _LiveAgent)

    openminion_root = _openminion_root()
    framework_root = _agent_framework_root()
    config_path = resolve_live_config_path(profile.config_path, framework_root)
    if not config_path.exists():
        pytest.skip(f"missing config file: {config_path}")
    agent_id = profile.agent_id or _resolve_agent_id(config_path, framework_root)

    os.environ.setdefault("OPENMINION_HOME", str(framework_root))
    # Keep live E2E data root aligned with OPENMINION_HOME to avoid temp-root drift
    # from the global pytest fixture when profile config paths are home-relative.
    os.environ["OPENMINION_DATA_ROOT"] = str(framework_root / ".openminion")

    runtime = APIRuntime.from_config_path(str(config_path))
    yield {
        "agent_id": agent_id,
        "config_path": config_path,
        "runtime": runtime,
        "openminion_root": openminion_root,
    }
    runtime.close()


@pytest.fixture(scope="session")
def live_openai_parity_agent() -> dict:
    _require_live_flag()

    profile = _OPENAI_PARITY_PROFILE
    openminion_root = _openminion_root()
    framework_root = _agent_framework_root()
    config_path = framework_root / "test-configs" / profile.config_path
    if not config_path.exists():
        pytest.skip(f"missing config file: {config_path}")
    agent_id = _resolve_agent_id(config_path, framework_root)

    os.environ.setdefault("OPENMINION_HOME", str(framework_root))
    os.environ["OPENMINION_DATA_ROOT"] = str(framework_root / ".openminion")

    runtime = APIRuntime.from_config_path(str(config_path))
    yield {
        "agent_id": agent_id,
        "config_path": config_path,
        "runtime": runtime,
        "openminion_root": openminion_root,
    }
    runtime.close()


def test_live_profile_matrix_resolves_default_agent_ids_from_current_configs() -> None:
    if _using_env_profile_overrides():
        pytest.skip("env-selected live tool targets override the built-in default set")
    framework_root = _agent_framework_root()
    resolved = {
        profile.profile_id: _resolve_agent_id(
            resolve_live_config_path(profile.config_path, framework_root),
            framework_root,
        )
        for profile in _PER_AGENT_PROFILES
    }
    assert resolved == {
        "openrouter-minimax": "hello-agent",
        "openrouter-claude-haiku": "hello-agent",
    }


def test_live_profile_matrix_bundle_agents_resolve_from_aggregate_configs() -> None:
    framework_root = _agent_framework_root()
    selected_bundle_targets = tuple(
        target[len("bundle:") :].strip()
        for target in str(os.getenv("OPENMINION_LIVE_TOOL_E2E_TARGETS", "")).split(",")
        if target.strip().startswith("bundle:")
    )
    if _using_env_profile_overrides() and not selected_bundle_targets:
        pytest.skip("env-selected live tool targets do not include bundle configs")
    bundle_names = (
        selected_bundle_targets
        if _using_env_profile_overrides()
        else ("agents-alibaba.json", "agents-openrouter.json")
    )
    bundle_agents = tuple(
        agent
        for bundle_name in bundle_names
        for agent in _agents_from_bundle(bundle_name, framework_root=framework_root)
    )
    assert bundle_agents, "selected live bundle configs must contain at least 1 agent"
    for agent in bundle_agents:
        assert agent.profile_id.startswith("bundle:"), agent.profile_id
        assert agent.agent_id is not None, agent.profile_id
        config_path = resolve_live_config_path(agent.config_path, framework_root)
        assert config_path.exists(), f"missing bundle config: {config_path}"
        config = load_config(str(config_path), home_root=framework_root)
        configured = configured_agent_ids(config)
        assert agent.agent_id in configured, (
            f"agent_id={agent.agent_id} not in configured ids={configured}"
        )


def test_live_tool_profile_provider_unavailable_classifier_covers_retired_models() -> (
    None
):
    assert _is_infra_unavailable_response(
        "The configured model provider failed before it could return a decision "
        "(No endpoints found for anthropic/claude-3.5-haiku.).",
        {},
        [],
    )
    assert _is_infra_unavailable_response(
        "Grok 4.1 Fast is deprecated. xAI recommends switching to Grok 4.3.",
        {},
        [],
    )


@pytest.mark.e2e
@pytest.mark.parametrize("scenario", _SCENARIOS, ids=[s.id for s in _SCENARIOS])
def test_live_tool_matrix(live_agent: dict, scenario: _Scenario) -> None:
    agent_id = str(live_agent["agent_id"])
    config_path = Path(live_agent["config_path"])
    runtime = live_agent["runtime"]
    _payload, metadata, tool_results, body = _run_live_turn_with_retries(
        agent_id=agent_id,
        config_path=config_path,
        runtime=runtime,
        scenario_id=scenario.id,
        message=scenario.message,
        target="e2e-tool-matrix",
        forced_tools=scenario.forced_tools,
    )

    assert tool_results, (
        f"no tool_results for agent={agent_id} scenario={scenario.id}\n"
        f"body={body}\nmetadata={json.dumps(metadata, indent=2, sort_keys=True)}"
    )

    executed_tool_names = {
        str(item.get("tool_name", "")).strip()
        for item in tool_results
        if str(item.get("tool_name", "")).strip()
    }
    assert executed_tool_names, (
        f"tool_results missing tool_name for agent={agent_id} scenario={scenario.id}\n"
        f"tool_results={json.dumps(tool_results, indent=2, sort_keys=True)}"
    )

    assert any(name in scenario.expected_tools for name in executed_tool_names), (
        f"unexpected tool for agent={agent_id} scenario={scenario.id}\n"
        f"expected_any={scenario.expected_tools}\nexecuted={sorted(executed_tool_names)}\n"
        f"tool_results={json.dumps(tool_results, indent=2, sort_keys=True)}"
    )

    assert not _RAW_TOOL_MARKUP_RE.search(body), (
        f"raw tool markup leaked in response body for agent={agent_id} scenario={scenario.id}\n"
        f"body={body}"
    )

    if scenario.require_source_tag:
        assert _SEARCH_SOURCE_MARKER_RE.search(body), (
            f"search response missing provider source marker for agent={agent_id} "
            f"scenario={scenario.id}\nbody={body}"
        )

    for token in scenario.forbidden_body_tokens:
        assert token.lower() not in body.lower(), (
            f"unexpected token '{token}' in body for agent={agent_id} scenario={scenario.id}\n"
            f"body={body}"
        )


@pytest.mark.e2e
@pytest.mark.parametrize(
    "scenario", _PARITY_SCENARIOS, ids=[s.id for s in _PARITY_SCENARIOS]
)
def test_live_openai_nl_tool_command_parity_matrix(
    live_openai_parity_agent: dict, scenario: _ParityScenario
) -> None:
    if _using_env_profile_overrides():
        pytest.skip(
            "env-selected live tool targets do not use the built-in parity agent"
        )
    agent_id = str(live_openai_parity_agent["agent_id"])
    config_path = Path(live_openai_parity_agent["config_path"])
    runtime = live_openai_parity_agent["runtime"]

    _payload, metadata, tool_results, body = _run_live_turn_with_retries(
        agent_id=agent_id,
        config_path=config_path,
        runtime=runtime,
        scenario_id=scenario.id,
        message=scenario.message,
        target="e2e-openai-parity",
        forced_tools=scenario.forced_tools,
    )

    assert tool_results, (
        f"no tool_results for agent={agent_id} scenario={scenario.id}\n"
        f"body={body}\nmetadata={json.dumps(metadata, indent=2, sort_keys=True)}"
    )
    assert (
        str(metadata.get("tool_loop_termination_reason", "")).strip() == "tool_final"
    ), (
        f"expected tool_final for agent={agent_id} scenario={scenario.id}\n"
        f"body={body}\nmetadata={json.dumps(metadata, indent=2, sort_keys=True)}"
    )

    executed_tool_names = {
        str(item.get("tool_name", "")).strip()
        for item in tool_results
        if str(item.get("tool_name", "")).strip()
    }
    assert any(name in scenario.expected_tools for name in executed_tool_names), (
        f"unexpected tool for agent={agent_id} scenario={scenario.id}\n"
        f"expected_any={scenario.expected_tools}\nexecuted={sorted(executed_tool_names)}\n"
        f"tool_results={json.dumps(tool_results, indent=2, sort_keys=True)}"
    )
    assert not _RAW_TOOL_MARKUP_RE.search(body), (
        f"raw tool markup leaked in response body for agent={agent_id} scenario={scenario.id}\n"
        f"body={body}"
    )
    if scenario.require_source_tag:
        assert _SEARCH_SOURCE_MARKER_RE.search(body), (
            f"search response missing provider source marker for agent={agent_id} "
            f"scenario={scenario.id}\nbody={body}"
        )
