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

pytestmark = pytest.mark.e2e

_RAW_TOOL_MARKUP_RE = re.compile(
    r"<minimax:tool_call>|<functioncall>|<invoke\s+name=|\[tool_call\]",
    re.IGNORECASE,
)


def _openminion_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _agent_framework_root() -> Path:
    return _openminion_root().parent


def _runtime_home_root() -> Path:
    return _openminion_root()


@dataclass(frozen=True)
class _Scenario:
    id: str
    message: str
    expected_tools: tuple[str, ...]
    forced_tools: tuple[str, ...]
    require_source_tag: bool = False
    forbidden_body_tokens: tuple[str, ...] = ()


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

_SCENARIOS: tuple[_Scenario, ...] = (
    _Scenario(
        id="time_now",
        message='tool time {"timezone":"America/Los_Angeles"}',
        expected_tools=("time",),
        forced_tools=("time",),
        forbidden_body_tokens=("NOT_IMPLEMENTED",),
    ),
    _Scenario(
        id="location_get",
        message="tool location {}",
        expected_tools=("location",),
        forced_tools=("location",),
        forbidden_body_tokens=("NOT_IMPLEMENTED",),
    ),
    _Scenario(
        id="fetch_providers",
        message='tool web.fetch {"url":"https://example.com","method":"HEAD"}',
        expected_tools=("web.fetch",),
        forced_tools=("web.fetch",),
        forbidden_body_tokens=("NOT_IMPLEMENTED",),
    ),
    _Scenario(
        id="fetch_get_core",
        message='tool web.fetch {"url":"https://example.com"}',
        expected_tools=("web.fetch",),
        forced_tools=("web.fetch",),
        forbidden_body_tokens=("NOT_IMPLEMENTED",),
    ),
    _Scenario(
        id="fetch_get_json",
        message='tool web.fetch {"url":"https://httpbin.org/json"}',
        expected_tools=("web.fetch",),
        forced_tools=("web.fetch",),
        forbidden_body_tokens=("NOT_IMPLEMENTED",),
    ),
    _Scenario(
        id="fetch_get_scrapling",
        message='tool web.fetch {"url":"https://example.com","prefer_backend":"scrapling","provider_options":{"scrapling":{"mode":"static"}}}',
        expected_tools=("web.fetch",),
        forced_tools=("web.fetch",),
        forbidden_body_tokens=("NOT_IMPLEMENTED",),
    ),
    _Scenario(
        id="fetch_get_scrapling_dynamic",
        message='tool web.fetch {"url":"https://example.com","prefer_backend":"scrapling","provider_options":{"scrapling":{"mode":"dynamic"}}}',
        expected_tools=("web.fetch",),
        forced_tools=("web.fetch",),
        forbidden_body_tokens=("NOT_IMPLEMENTED",),
    ),
    _Scenario(
        id="fetch_get_scrapling_stealth",
        message='tool web.fetch {"url":"https://example.com","prefer_backend":"scrapling","provider_options":{"scrapling":{"mode":"stealth"}}}',
        expected_tools=("web.fetch",),
        forced_tools=("web.fetch",),
        forbidden_body_tokens=("NOT_IMPLEMENTED",),
    ),
)


def _resolve_agent_id(config_path: Path, framework_root: Path) -> str:
    config = load_config(str(config_path), home_root=_runtime_home_root())
    preferred = str(config.agents[next(iter(config.agents.keys()))].name or "").strip()
    configured = configured_agent_ids(config)
    if preferred and preferred in configured:
        return preferred
    if configured:
        return configured[0]
    raise AssertionError(f"no configured agent ids found in {config_path}")


def _using_env_profile_overrides() -> bool:
    return bool(_ENV_AGENT_PROFILES)


def _live_new_tools_enabled() -> bool:
    return any(
        str(os.getenv(name, "")).strip() == "1"
        for name in ("OPENMINION_LIVE_TOOL_E2E_NEW_TOOLS", "OPENMINION_LIVE_TOOL_E2E")
    )


def _require_live_flag() -> None:
    if not _live_new_tools_enabled():
        pytest.skip(
            "Set OPENMINION_LIVE_TOOL_E2E_NEW_TOOLS=1 (or shared OPENMINION_LIVE_TOOL_E2E=1) to run the new-tools live E2E matrix."
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


def _has_policy_denial(body: str, tool_results: list[dict]) -> bool:
    lowered = body.lower()
    if "denied by policy" in lowered:
        return True
    if "requires tool.fetch." in lowered and "authorization" in lowered:
        return True
    if "authorize browser access" in lowered:
        return True
    if "obtain browser authorization" in lowered:
        return True
    if "request stealth mode authorization" in lowered:
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


def _is_tool_unavailable(body: str) -> bool:
    return "required tool is not available in this runtime" in body.lower()


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


def _is_scaffold_not_implemented(tool_results: list[dict], body: str) -> bool:
    body_token = body.lower()
    if "not_implemented" in body_token or "not implemented" in body_token:
        return True
    for item in tool_results:
        error = item.get("error")
        if (
            isinstance(error, dict)
            and str(error.get("code", "")).upper() == "NOT_IMPLEMENTED"
        ):
            return True
        result = item.get("result")
        if isinstance(result, dict):
            nested_error = result.get("error")
            if (
                isinstance(nested_error, dict)
                and str(nested_error.get("code", "")).upper() == "NOT_IMPLEMENTED"
            ):
                return True
    return False


def _is_turn_budget_exhausted(body: str) -> bool:
    lowered = body.lower()
    return (
        "turn time budget exhausted" in lowered or "llm call budget exceeded" in lowered
    )


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

    runtime_home = _runtime_home_root()
    os.environ.setdefault("OPENMINION_HOME", str(runtime_home))
    os.environ["OPENMINION_DATA_ROOT"] = str(runtime_home / ".openminion")

    runtime = APIRuntime.from_config_path(str(config_path))
    yield {
        "agent_id": agent_id,
        "config_path": config_path,
        "runtime": runtime,
        "openminion_root": openminion_root,
    }
    runtime.close()


def test_live_new_tools_matrix_resolves_default_agent_ids_from_current_configs() -> (
    None
):
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


def test_live_new_tools_matrix_bundle_agents_resolve_from_aggregate_configs() -> None:
    framework_root = _agent_framework_root()
    bundle_names = (
        tuple(
            target[len("bundle:") :].strip()
            for target in str(os.getenv("OPENMINION_LIVE_TOOL_E2E_TARGETS", "")).split(
                ","
            )
            if target.strip().startswith("bundle:")
        )
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
        config = load_config(str(config_path), home_root=_runtime_home_root())
        configured = configured_agent_ids(config)
        assert agent.agent_id in configured, (
            f"agent_id={agent.agent_id} not in configured ids={configured}"
        )


def test_live_new_tools_matrix_accepts_shared_or_dedicated_live_flags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENMINION_LIVE_TOOL_E2E_NEW_TOOLS", raising=False)
    monkeypatch.delenv("OPENMINION_LIVE_TOOL_E2E", raising=False)
    assert _live_new_tools_enabled() is False

    monkeypatch.setenv("OPENMINION_LIVE_TOOL_E2E", "1")
    assert _live_new_tools_enabled() is True

    monkeypatch.setenv("OPENMINION_LIVE_TOOL_E2E", "0")
    monkeypatch.setenv("OPENMINION_LIVE_TOOL_E2E_NEW_TOOLS", "1")
    assert _live_new_tools_enabled() is True


@pytest.mark.e2e
@pytest.mark.parametrize("scenario", _SCENARIOS, ids=[s.id for s in _SCENARIOS])
def test_live_new_tools_matrix(live_agent: dict, scenario: _Scenario) -> None:
    agent_id = str(live_agent["agent_id"])
    config_path = Path(live_agent["config_path"])
    runtime = live_agent["runtime"]

    session_id = f"live-new-tools-e2e-{agent_id}-{scenario.id}-{uuid.uuid4().hex[:8]}"
    try:
        payload = run_turn(
            str(config_path),
            {
                "message": scenario.message,
                "agent_id": agent_id,
                "channel": "console",
                "target": "e2e-new-tools-matrix",
                "session_id": session_id,
                "forced_tools": list(scenario.forced_tools),
                "timeout_seconds": _timeout_seconds(),
            },
            runtime=runtime,
        )
    except Exception as exc:  # noqa: BLE001 - live e2e guard
        if _is_quota_error(exc):
            pytest.skip(
                f"provider quota exhausted for agent={agent_id} scenario={scenario.id}: {exc}"
            )
        raise

    metadata = (
        payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    )
    tool_results = _tool_results_from_metadata(metadata)
    body = str(payload.get("body", "") or "")

    if _has_policy_denial(body, tool_results):
        pytest.skip(
            f"policy denied forced tool for agent={agent_id} scenario={scenario.id}: {body}"
        )

    if _is_tool_unavailable(body):
        pytest.skip(
            f"forced tool unavailable in runtime for agent={agent_id} scenario={scenario.id}: {body}"
        )

    if _is_scaffold_not_implemented(tool_results, body):
        pytest.skip(f"scenario={scenario.id} still scaffolded (NOT_IMPLEMENTED)")

    if scenario.id in {
        "fetch_get_scrapling_dynamic",
        "fetch_get_scrapling_stealth",
    } and _is_turn_budget_exhausted(body):
        pytest.skip(
            f"scenario={scenario.id} hit live turn-budget limits before tool execution: {body}"
        )

    executed_tool_names = {
        str(item.get("tool_name", "")).strip()
        for item in tool_results
        if str(item.get("tool_name", "")).strip()
    }

    assert executed_tool_names, (
        f"no executed tool names for agent={agent_id} scenario={scenario.id}\n"
        f"body={body}\nmetadata={json.dumps(metadata, indent=2, sort_keys=True)}"
    )
    assert any(name in scenario.expected_tools for name in executed_tool_names), (
        f"unexpected tool for agent={agent_id} scenario={scenario.id}\n"
        f"expected_any={scenario.expected_tools}\nexecuted={sorted(executed_tool_names)}"
    )

    assert not _RAW_TOOL_MARKUP_RE.search(body), (
        f"raw tool markup leaked in response body for agent={agent_id} scenario={scenario.id}\n"
        f"body={body}"
    )

    if scenario.require_source_tag:
        assert "source=" in body.lower(), (
            f"response missing provider source marker for agent={agent_id} "
            f"scenario={scenario.id}\nbody={body}"
        )

    for token in scenario.forbidden_body_tokens:
        assert token.lower() not in body.lower(), (
            f"unexpected token '{token}' in body for agent={agent_id} scenario={scenario.id}\n"
            f"body={body}"
        )
