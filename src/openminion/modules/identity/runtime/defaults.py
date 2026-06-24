import re

_SENTENCE_RE = re.compile(r"^([^.!?]+[.!?])")


def normalize_identity_text(value: str) -> str:
    return " ".join(str(value or "").strip().split())


def default_mission(*, agent_id: str, system_prompt: str) -> str:
    text = normalize_identity_text(system_prompt)
    if text:
        match = _SENTENCE_RE.search(text)
        if match:
            sentence = normalize_identity_text(match.group(1))
            if sentence:
                return sentence[:120].rstrip() + ("..." if len(sentence) > 120 else "")
        return text[:120].rstrip() + ("..." if len(text) > 120 else "")
    return f"I am {normalize_identity_text(agent_id)}, a pragmatic AI assistant."


__all__ = ["default_mission", "normalize_identity_text"]
