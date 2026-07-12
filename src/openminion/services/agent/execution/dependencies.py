from dataclasses import dataclass
from typing import Any, Callable

from openminion.modules.tool.base import ToolExecutionResult

FinalizeResponse = Callable[[Any], Any]
ToolPayloadFn = Callable[[list[Any]], str]
LooksLikeEnvelopeFn = Callable[[str], bool]
IdentityMetadataFn = Callable[[], dict[str, str]]
ToolBatchMetadataFn = Callable[..., dict[str, str]]
CollectMissingRequiredFn = Callable[..., dict[str, list[str]]]
IsArgErrorFn = Callable[[ToolExecutionResult], bool]
ExtractMissingFieldsFn = Callable[[list[ToolExecutionResult]], str]
CanonicalToolNameFn = Callable[[str], str]


@dataclass(slots=True)
class ExecutorDeps:
    finalize_response: FinalizeResponse
    tool_calls_payload: ToolPayloadFn
    looks_like_tool_call_envelope: LooksLikeEnvelopeFn
    identity_metadata: IdentityMetadataFn
    tool_batch_metadata: ToolBatchMetadataFn
    collect_missing_required_args: CollectMissingRequiredFn
    is_tool_argument_error: IsArgErrorFn
    extract_missing_argument_fields: ExtractMissingFieldsFn
    canonical_tool_name: CanonicalToolNameFn
