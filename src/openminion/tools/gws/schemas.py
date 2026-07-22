"""Google Workspace tool schemas."""

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

RiskLevel = Literal["read", "write", "admin"]
RedactionMode = Literal["none", "basic", "strict"]


class GwsPaginationArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page_all: bool = False
    page_limit: Optional[int] = Field(default=None, ge=1, le=10_000)
    page_delay_ms: Optional[int] = Field(default=None, ge=0, le=300_000)


class GwsCallArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    service: str = Field(
        ..., min_length=1, description="Google API service, e.g. drive/gmail/calendar."
    )
    resource_path: list[str] = Field(
        default_factory=list, description="Resource path tokens before method."
    )
    method: str = Field(
        ..., min_length=1, description="Method token, e.g. list/get/create/delete."
    )
    params: Optional[dict[str, Any]] = Field(
        default=None, description="Maps to --params '<json>'."
    )
    json_payload: Optional[dict[str, Any]] = Field(
        default=None, alias="json", description="Maps to --json '<json>'."
    )
    dry_run: bool = Field(default=False, description="If true, include --dry-run.")
    pagination: Optional[GwsPaginationArgs] = None
    timeout_seconds: Optional[float] = Field(default=None, gt=0, le=3600)
    expect_large_output: bool = False
    force_risk: Optional[RiskLevel] = None
    redaction_mode: Optional[RedactionMode] = None

    @field_validator("service", "method", mode="before")
    @classmethod
    def _normalize_required_strings(cls, value: Any) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("value is required")
        return normalized

    @field_validator("resource_path", mode="before")
    @classmethod
    def _normalize_resource_path(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("resource_path must be an array of strings")
        rows: list[str] = []
        for token in value:
            normalized = str(token or "").strip()
            if not normalized:
                raise ValueError("resource_path entries must not be empty")
            rows.append(normalized)
        return rows


class GwsSchemaArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    method_full: str = Field(
        ..., min_length=1, description="Method id, e.g. drive.files.list"
    )
    timeout_seconds: Optional[float] = Field(default=None, gt=0, le=3600)
    redaction_mode: Optional[RedactionMode] = None

    @field_validator("method_full", mode="before")
    @classmethod
    def _normalize_method_full(cls, value: Any) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("method_full is required")
        return normalized


class GwsAuthArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timeout_seconds: Optional[float] = Field(default=None, gt=0, le=3600)
    redaction_mode: Optional[RedactionMode] = None


class GwsAuthExportArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    output_path: str = Field(
        ...,
        min_length=1,
        description="Path where exported credentials should be written.",
    )
    overwrite: bool = False
    timeout_seconds: Optional[float] = Field(default=None, gt=0, le=3600)
    redaction_mode: Optional[RedactionMode] = None

    @field_validator("output_path", mode="before")
    @classmethod
    def _normalize_output_path(cls, value: Any) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("output_path is required")
        return normalized


class GwsEnvConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    credentials_file_secret: Optional[str] = None
    token_secret: Optional[str] = None
    impersonated_user: Optional[str] = None
    credentials_file: Optional[str] = None
    token: Optional[str] = None

    @field_validator(
        "credentials_file_secret",
        "token_secret",
        "impersonated_user",
        "credentials_file",
        "token",
        mode="before",
    )
    @classmethod
    def _normalize_strings(cls, value: Any) -> Optional[str]:
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None


class GwsDefaultsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timeout_seconds: float = Field(default=30.0, gt=0.0, le=3600.0)
    page_limit: int = Field(default=10, ge=1, le=10_000)
    page_delay_ms: int = Field(default=100, ge=0, le=300_000)
    allow_page_all: bool = True


class GwsSafetyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    require_prompt_for_write: bool = True
    require_prompt_for_admin: bool = True
    deny_services: list[str] = Field(default_factory=list)
    redaction_mode: RedactionMode = "basic"

    @field_validator("deny_services", mode="before")
    @classmethod
    def _normalize_deny_services(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("deny_services must be an array")
        rows: list[str] = []
        for item in value:
            normalized = str(item or "").strip().lower()
            if normalized and normalized not in rows:
                rows.append(normalized)
        return rows


class GwsToolConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    gws_path: str = "gws"
    env: GwsEnvConfig = Field(default_factory=GwsEnvConfig)
    defaults: GwsDefaultsConfig = Field(default_factory=GwsDefaultsConfig)
    safety: GwsSafetyConfig = Field(default_factory=GwsSafetyConfig)
    max_output_parse_bytes: int = Field(default=2_000_000, ge=1024, le=100_000_000)
    max_raw_stdout_bytes: int = Field(default=200_000, ge=0, le=100_000_000)
    max_raw_stderr_bytes: int = Field(default=200_000, ge=0, le=100_000_000)

    @field_validator("gws_path", mode="before")
    @classmethod
    def _normalize_gws_path(cls, value: Any) -> str:
        normalized = str(value or "").strip()
        return normalized or "gws"


GWS_CALL_INPUT_SCHEMA: dict[str, Any] = GwsCallArgs.model_json_schema(by_alias=True)
GWS_SCHEMA_INPUT_SCHEMA: dict[str, Any] = GwsSchemaArgs.model_json_schema()
GWS_AUTH_INPUT_SCHEMA: dict[str, Any] = GwsAuthArgs.model_json_schema()
GWS_AUTH_EXPORT_INPUT_SCHEMA: dict[str, Any] = GwsAuthExportArgs.model_json_schema()

GWS_RESULT_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["ok", "source", "content"],
    "properties": {
        "ok": {"type": "boolean"},
        "source": {"type": "string"},
        "content": {"type": "string"},
        "data": {},
        "data_format": {"type": "string"},
        "raw_stdout": {"type": ["string", "null"]},
        "raw_stderr": {"type": "string"},
        "error": {
            "type": ["object", "null"],
            "properties": {
                "code": {"type": "string"},
                "message": {"type": "string"},
                "details": {"type": "object"},
            },
            "required": ["code", "message"],
            "additionalProperties": True,
        },
        "metrics": {"type": "object"},
    },
    "additionalProperties": True,
}
