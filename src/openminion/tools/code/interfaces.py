from typing import Any, Dict, Literal

from pydantic import BaseModel, ConfigDict, Field

from openminion.modules.tool import (
    PLUGIN_CONTRACT_VERSION,
    ContractValidator,
    ToolErrorEnvelope,
    ToolRequestEnvelope,
    ToolResultEnvelope,
)


CODE_PLUGIN_INTERFACE_VERSION = PLUGIN_CONTRACT_VERSION

CONTRACT_VERSION = CODE_PLUGIN_INTERFACE_VERSION
validate_contract_version = ContractValidator.validate_contract_version
is_compatible = ContractValidator.is_compatible


class CodeRequestEnvelope(ToolRequestEnvelope):
    pass


class CodeResultEnvelope(ToolResultEnvelope):
    pass


class CodeErrorEnvelope(ToolErrorEnvelope):
    pass


class CodeOperationSchema(BaseModel):
    operation: str
    parameters: Dict[str, Any]
    contract_version: str = CODE_PLUGIN_INTERFACE_VERSION


class RepoIndexSymbol(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    kind: Literal["class", "function"] = "function"
    file: str = Field(min_length=1)
    start_line: int = Field(default=1, ge=1)
    end_line: int = Field(default=1, ge=1)


class RepoIndexImport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    importer: str = Field(min_length=1)
    module: str = Field(min_length=1)
    imported_names: list[str] = Field(default_factory=list)


class RepoIndexFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1)
    language: str = Field(default="unknown", min_length=1)
    top_level_symbols: list[str] = Field(default_factory=list)
    imports: list[str] = Field(default_factory=list)


class RepoIndex(BaseModel):
    model_config = ConfigDict(extra="forbid")

    root: str = Field(min_length=1)
    files: list[RepoIndexFile] = Field(default_factory=list)
    symbols: list[RepoIndexSymbol] = Field(default_factory=list)
    imports: list[RepoIndexImport] = Field(default_factory=list)
