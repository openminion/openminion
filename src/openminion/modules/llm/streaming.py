from collections.abc import Iterator

from .errors import LLMCtlError
from .schemas import LLMResponse, LLMStreamEvent, ResponseError


def stream_error_event(exc: Exception, *, message_prefix: str = "") -> LLMStreamEvent:
    if isinstance(exc, LLMCtlError):
        return LLMStreamEvent(
            type="error",
            error=ResponseError(
                code=exc.code,
                message=f"{message_prefix}{exc.message}",
                details=dict(exc.details or {}),
            ),
        )
    return LLMStreamEvent(
        type="error",
        error=ResponseError(
            code="PROVIDER_ERROR",
            message=f"provider stream raised: {exc}",
        ),
    )


def response_stream_events(response: LLMResponse) -> Iterator[LLMStreamEvent]:
    if response.output_text:
        yield LLMStreamEvent(type="delta", delta_text=response.output_text)
    for tool_call in response.tool_calls:
        yield LLMStreamEvent(type="delta", tool_call=tool_call)
    if response.error is not None:
        yield LLMStreamEvent(type="error", error=response.error)
