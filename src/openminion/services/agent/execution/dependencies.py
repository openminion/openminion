from dataclasses import dataclass
from typing import Any, Callable

FinalizeResponse = Callable[[Any], Any]
IdentityMetadataFn = Callable[[], dict[str, str]]
ToolBatchMetadataFn = Callable[..., dict[str, str]]


@dataclass(slots=True)
class ExecutorDeps:
    finalize_response: FinalizeResponse
    identity_metadata: IdentityMetadataFn
    tool_batch_metadata: ToolBatchMetadataFn
