from __future__ import annotations

from openminion.modules.llm.providers.behavior import resolve_behavior_profile
from openminion.modules.llm.providers.behavior.constants import (
    DEFAULT_REQUEST_DIALECT,
    MINIMAX_OPENAI_COMPAT_REQUEST_DIALECT,
)
from openminion.modules.llm.providers.openai.request_compatibility import (
    resolve_openai_request_compat,
)


def test_minimax_dialect_alone_selects_minimax_compat_profile():
    profile = resolve_openai_request_compat(
        request_dialect=MINIMAX_OPENAI_COMPAT_REQUEST_DIALECT
    )
    assert profile.profile_id == "minimax_openai_compat"
    assert profile.collapse_system_messages is True
    assert profile.disable_fallback_instruction is True
    assert profile.enable_structured_tool_envelope_parse is True


def test_default_dialect_alone_selects_default_profile():
    profile = resolve_openai_request_compat(request_dialect=DEFAULT_REQUEST_DIALECT)
    assert profile.profile_id == "openai_default"
    assert profile.collapse_system_messages is False


def test_resolved_behavior_profile_supplies_correct_dialect_for_minimax_endpoint():
    profile = resolve_behavior_profile(
        provider="openai",
        model="minimax-m2",
        base_url="https://api.minimax.io/v1",
    )
    assert profile.request_dialect == MINIMAX_OPENAI_COMPAT_REQUEST_DIALECT

    # Resolve compat via dialect ONLY (no provider_identity passed).
    compat = resolve_openai_request_compat(request_dialect=profile.request_dialect)
    assert compat.profile_id == "minimax_openai_compat"


def test_resolved_behavior_profile_supplies_correct_dialect_for_dashscope_qwen():
    profile = resolve_behavior_profile(
        provider="openai",
        model="qwen-plus",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    )
    assert profile.request_dialect == DEFAULT_REQUEST_DIALECT

    compat = resolve_openai_request_compat(request_dialect=profile.request_dialect)
    assert compat.profile_id == "openai_default"


def test_dialect_path_and_identity_path_produce_identical_minimax_profile():
    via_dialect = resolve_openai_request_compat(
        request_dialect=MINIMAX_OPENAI_COMPAT_REQUEST_DIALECT,
    )
    via_identity_only = resolve_openai_request_compat(
        provider_identity={
            "transport_adapter": "openai_chat",
            "wire_protocol_family": "openai_chat_completions",
            "service_vendor": "minimax",
            "model_family": "minimax",
        },
    )
    assert via_dialect.profile_id == via_identity_only.profile_id
    assert (
        via_dialect.collapse_system_messages
        == via_identity_only.collapse_system_messages
    )
    assert (
        via_dialect.enable_structured_tool_envelope_parse
        == via_identity_only.enable_structured_tool_envelope_parse
    )
