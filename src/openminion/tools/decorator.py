import inspect
import types
from typing import Any, Optional, cast, get_type_hints, overload
from collections.abc import Callable, Mapping

from pydantic import BaseModel, ConfigDict, create_model
from pydantic.fields import FieldInfo


def _exposed_parameters(signature: inspect.Signature) -> dict[str, inspect.Parameter]:
    """Parameters surfaced in the args model: no self/cls and no variadics."""
    return {
        name: param
        for name, param in signature.parameters.items()
        if name not in ("self", "cls")
        and param.kind
        not in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
    }


def _resolve_postponed_annotations(
    func: Callable[..., Any], parameters: Mapping[str, inspect.Parameter]
) -> dict[str, Any]:
    """Resolve string annotations (PEP 563 or quoted) of exposed parameters.

    Only the parameters that become args-model fields are evaluated, in the
    function's module namespace. Return, self/cls and variadic annotations are
    never consumed by the args model, so they are left unevaluated.
    ``include_extras=True`` keeps ``Annotated`` metadata such as ``Field``
    descriptions and constraints. An exposed name that cannot be resolved
    raises instead of producing an args model that pydantic cannot build.
    """
    postponed = {
        name: param.annotation
        for name, param in parameters.items()
        if isinstance(param.annotation, str)
    }
    if not postponed:
        return {}
    exposed = types.SimpleNamespace(__annotations__=postponed)
    globalns = getattr(inspect.unwrap(func), "__globals__", {})
    try:
        return get_type_hints(exposed, globalns=globalns, include_extras=True)
    except NameError as exc:
        qualname = getattr(func, "__qualname__", repr(func))
        raise TypeError(
            f"@tool could not resolve the argument annotations of {qualname!r}: "
            f"{exc}. Argument annotation names must be importable from the "
            "function's module."
        ) from exc


def _build_args_model(func: Callable[..., Any], model_name: str) -> type[BaseModel]:
    signature = inspect.signature(func)
    parameters = _exposed_parameters(signature)
    resolved_annotations = _resolve_postponed_annotations(func, parameters)
    fields: dict[str, Any] = {}
    for name, param in parameters.items():
        annotation = resolved_annotations.get(name, param.annotation)
        if annotation is inspect.Parameter.empty:
            annotation = Any
        if param.default is inspect.Parameter.empty:
            # No Python default: honour ``Field(default=...)`` /
            # ``Field(default_factory=...)`` inside ``Annotated`` instead of
            # overriding it with the ``...`` required marker.
            field_info = FieldInfo.from_annotation(annotation)
            fields[name] = (field_info.annotation, field_info)
        else:
            # A Python default (plain value or ``Field(...)``) is passed to
            # ``create_model`` unchanged; pydantic resolves it natively.
            fields[name] = (annotation, param.default)
    return create_model(
        model_name,
        __config__=ConfigDict(extra="forbid"),
        **fields,
    )


# ``model_validate(..., by_alias=True, by_name=True)`` exists from pydantic
# 2.11; the package floor is ``pydantic>=2``.
_MODEL_VALIDATE_ACCEPTS_BY_NAME = (
    "by_name" in inspect.signature(BaseModel.model_validate).parameters
)


def _alias_or_name_validator(
    args_model: type[BaseModel],
) -> Callable[[Mapping[str, Any]], BaseModel]:
    """Validate a handler mapping keyed by declared aliases or field names.

    The args model itself is unchanged; only the decorator handler accepts
    both key forms. Pydantic releases before 2.11 have no per-call
    ``by_name``; a handler-only subclass with ``populate_by_name`` covers the
    args model's own fields, but not the field names of a nested model that
    declares aliases, whose configuration belongs to the caller.
    """
    if _MODEL_VALIDATE_ACCEPTS_BY_NAME:

        def _validate(values: Mapping[str, Any]) -> BaseModel:
            return args_model.model_validate(values, by_alias=True, by_name=True)

        return _validate
    by_name_model = cast(
        type[BaseModel],
        type(
            args_model.__name__,
            (args_model,),
            {
                "model_config": ConfigDict(populate_by_name=True),
                "__module__": args_model.__module__,
            },
        ),
    )
    return by_name_model.model_validate


def _infer_description(func: Callable[..., Any]) -> str:
    raw_name = str(getattr(func, "__name__", "") or "").strip("_")
    words = [part for part in raw_name.replace("-", "_").split("_") if part]
    if not words:
        return ""

    subject = words[0].capitalize()
    parameters = [
        param
        for name, param in inspect.signature(func).parameters.items()
        if name not in {"self", "cls"}
        and param.kind
        not in {
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        }
    ]
    if not parameters:
        return f"{subject}."

    def _annotation_name(annotation: Any) -> str:
        if annotation is inspect.Parameter.empty:
            return ""
        if isinstance(annotation, str):
            return annotation.strip().lower()
        return str(getattr(annotation, "__name__", annotation)).strip().lower()

    annotation_names = [_annotation_name(param.annotation) for param in parameters]
    if all(name == "int" for name in annotation_names):
        quantity = {
            1: "an integer",
            2: "two integers",
        }.get(len(parameters), f"{len(parameters)} integers")
        return f"{subject} {quantity}."

    if all(name == "str" for name in annotation_names):
        quantity = {
            1: "a string",
            2: "two strings",
        }.get(len(parameters), f"{len(parameters)} strings")
        return f"{subject} {quantity}."

    return f"{subject}."


def _user_tool_exposure_profile_id(tool_name: str) -> str:
    """Map a tool name to a profile id injectively.

    Profile ids only allow alphanumerics, ``_`` and ``-``. Alphanumerics and
    ``-`` pass through, ``_`` is escaped as ``__`` and any other character as
    ``_x<hex>_``, so distinct tool names (e.g. ``lookup-order`` and
    ``lookup_order``) never share a profile id.
    """
    encoded: list[str] = []
    for char in tool_name.strip():
        if char == "_":
            encoded.append("__")
        elif char == "-" or char.isalnum():
            encoded.append(char)
        else:
            encoded.append(f"_x{ord(char):x}_")
    return f"user_tool_{''.join(encoded) or 'tool'}"


def _decorate(
    func: Callable[..., Any],
    *,
    name: Optional[str],
    description: Optional[str],
    min_scope: Optional[str],
    dangerous: bool,
    idempotent: bool,
    tags: tuple[str, ...],
    capabilities: tuple[str, ...],
) -> Callable[..., Any]:
    from openminion.modules.tool.exposure import ToolExposureProfile
    from openminion.modules.tool.framework import ToolDecl, ToolFamilySpec

    resolved_name = (name or func.__name__).strip()
    resolved_description = (
        description or (func.__doc__ or "").strip() or _infer_description(func)
    ).strip()
    args_model = _build_args_model(func, f"{resolved_name.replace('.', '_')}Args")

    validate_args = _alias_or_name_validator(args_model)
    signature = inspect.signature(func)
    positional_fields = tuple(
        name
        for name, param in signature.parameters.items()
        if param.kind is inspect.Parameter.POSITIONAL_ONLY
        and name in args_model.model_fields
    )

    def _handler(
        args: BaseModel | Mapping[str, Any],
        context: Any = None,
    ) -> Any:
        del context
        # Pass the validated field values, not ``model_dump()``: dumping turns
        # nested BaseModel / dataclass arguments back into plain dicts. The
        # runtime hands over an already-dumped mapping (canonical field names)
        # and callers may pass declared aliases, so re-validate accepting both.
        validated = (
            args
            if isinstance(args, args_model)
            else validate_args(
                args.model_dump() if isinstance(args, BaseModel) else dict(args)
            )
        )
        positional = [getattr(validated, name) for name in positional_fields]
        keywords = {
            name: getattr(validated, name)
            for name in args_model.model_fields
            if name not in positional_fields
        }
        return func(*positional, **keywords)

    decl = ToolDecl(
        name=resolved_name,
        args_model=args_model,
        handler=_handler,
        description=resolved_description,
        min_scope=min_scope,
        dangerous=dangerous,
        idempotent=idempotent,
        tags=tags,
        capabilities=capabilities,
    )

    def _family_spec(
        module_id: Optional[str] = None,
        *,
        min_scope_default: str = "WRITE_SAFE",
    ) -> ToolFamilySpec:
        return ToolFamilySpec(
            module_id=module_id or f"openminion.tools.user.{resolved_name}",
            tools=(decl,),
            min_scope_default=min_scope_default,
            # User-decorated tools are not in the built-in default-visible
            # catalog, so without an active exposure profile the exposure
            # service hides them from the model and denies their execution.
            exposure_profiles=(
                ToolExposureProfile(
                    profile_id=_user_tool_exposure_profile_id(resolved_name),
                    title=f"User tool: {resolved_name}",
                    summary="Tool explicitly supplied by the caller via @openminion.tool.",
                    tool_names=frozenset({resolved_name}),
                    default_active=True,
                ),
            ),
        )

    func.tool_decl = decl  # type: ignore[attr-defined]
    func.tool_family_spec = _family_spec  # type: ignore[attr-defined]
    func.tool_args_model = args_model  # type: ignore[attr-defined]
    return func


@overload
def tool(
    func: Callable[..., Any],
    *,
    name: Optional[str] = ...,
    description: Optional[str] = ...,
    min_scope: Optional[str] = ...,
    dangerous: bool = ...,
    idempotent: bool = ...,
    tags: tuple[str, ...] = ...,
    capabilities: tuple[str, ...] = ...,
) -> Callable[..., Any]: ...


@overload
def tool(
    *,
    name: Optional[str] = ...,
    description: Optional[str] = ...,
    min_scope: Optional[str] = ...,
    dangerous: bool = ...,
    idempotent: bool = ...,
    tags: tuple[str, ...] = ...,
    capabilities: tuple[str, ...] = ...,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]: ...


def tool(
    func: Callable[..., Any] | None = None,
    *,
    name: Optional[str] = None,
    description: Optional[str] = None,
    min_scope: Optional[str] = None,
    dangerous: bool = False,
    idempotent: bool = False,
    tags: tuple[str, ...] = (),
    capabilities: tuple[str, ...] = (),
) -> Any:
    """Decorate a Python function as an OpenMinion tool."""

    def _wrap(actual: Callable[..., Any]) -> Callable[..., Any]:
        return _decorate(
            actual,
            name=name,
            description=description,
            min_scope=min_scope,
            dangerous=dangerous,
            idempotent=idempotent,
            tags=tags,
            capabilities=capabilities,
        )

    return _wrap(func) if func is not None else _wrap
