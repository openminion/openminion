from dataclasses import dataclass

from openminion.modules.memory.errors import InvalidArgumentError

CONTEXT_SESSION_START_RECALL_LIMIT = 6
CONTEXT_MID_SESSION_RECALL_LIMIT = 4
CONTEXT_MID_SESSION_RECALL_INTERVAL = 3
CONTEXT_MID_SESSION_RECALL_NOVELTY_THRESHOLD = 0.3
CONTEXT_RECENT_SESSION_ARTIFACT_LIMIT = 6
CONTEXT_RECENT_SESSION_ARTIFACT_MAX_AGE_DAYS = 14
CONTEXT_IMPROVEMENT_NOTE_LIMIT = 6
CONTEXT_STRATEGY_OUTCOME_LIMIT = 6
CONTEXT_POST_COMPLETION_CRITIQUE_LIMIT = 6

LONGLINGUA_DEFAULT_SEGMENT_SIZE = 3
SELECTIVE_CONTEXT_DUPLICATE_THRESHOLD = 0.7
RECOMP_EXTRACTIVE_DEFAULT_TOP_N = 2


@dataclass(frozen=True)
class ContextConfig:
    compaction_trigger_percent: float = 0.85

    def __post_init__(self) -> None:
        if not 0.0 < float(self.compaction_trigger_percent) <= 1.0:
            raise InvalidArgumentError(
                "compaction_trigger_percent must be within (0.0, 1.0]"
            )


def load_config(*_args: object, **_kwargs: object) -> ContextConfig:
    return ContextConfig()
