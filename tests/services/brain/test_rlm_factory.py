from __future__ import annotations

from types import SimpleNamespace

from openminion.services.brain.factory import rlm


class _Logger:
    def info(self, *_args, **_kwargs) -> None:
        pass

    def warning(self, *_args, **_kwargs) -> None:
        pass

    def debug(self, *_args, **_kwargs) -> None:
        pass


class _Skill:
    def match(self, intent_text, step_hint, agent_id, k=3, status_filter=None):
        del intent_text, step_hint, agent_id, k, status_filter
        return [
            {
                "skill_id": "linear",
                "version_hash": "v" * 64,
                "name": "Linear",
                "score": 0.9,
                "tags": ["issues"],
            }
        ]

    def render_snippet(self, skill_id, version_hash, purpose, max_tokens):
        del version_hash, purpose, max_tokens
        return (f"Skill snippet for {skill_id}", "s" * 64)


def test_init_rlm_adapter_wires_skill_client_into_real_service(monkeypatch) -> None:
    monkeypatch.setattr(rlm, "create_rlm_adapter", lambda **kwargs: kwargs["service"])

    skill_api = _Skill()
    service = rlm.init_rlm_adapter(
        mode="auto",
        config=SimpleNamespace(rlm=SimpleNamespace(enabled=True)),
        session_api=object(),
        context_api=object(),
        llm_api=object(),
        memory_api=None,
        skill_api=skill_api,
        retrieve_api=None,
        logger=_Logger(),
    )

    assert service._skillctl is skill_api

    rows = service._retrieve_skills(
        agent_id="agent-skill",
        query="triage a linear issue",
        strategy="auto",
    )

    assert len(rows) == 1
    assert rows[0].source == "skill"
    assert rows[0].ref_id.startswith("linear@")
    assert rows[0].metadata["skill_name"] == "Linear"
