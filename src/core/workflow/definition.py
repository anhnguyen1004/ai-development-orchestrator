"""Declarative WorkflowDefinition and WorkflowInputDefinition models."""

import re
from typing import Any, Mapping
from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator, model_validator

from src.core.contracts.artifact import Sha256Hash
from src.core.contracts.ids import NonEmptyStr
from src.core.workflow.freezer import freeze_value, unfreeze_value, validate_json_serializable
from src.core.workflow.hashing import compute_canonical_workflow_hash
from src.core.workflow.step_definition import StepDefinition

# Strict SemVer 2.0.0 without whitespace tolerance and without leading zeros in numeric segments
SEMVER_REGEX = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?"
    r"(?:\+([0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$"
)


class WorkflowInputDefinition(BaseModel):
    """Declarative specification of an input value supplied at workflow initialization."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    key: NonEmptyStr = Field(description="Unique state key for this input")
    description: str = Field(default="", description="Human-readable description of input")
    required: bool = Field(default=True, description="Whether this input must be supplied at run start")


class WorkflowDefinition(BaseModel):
    """Declarative, versioned workflow template."""

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    workflow_id: NonEmptyStr
    name: NonEmptyStr
    version: str = Field(description="Strict SemVer 2.0.0 string e.g. '1.0.0'")
    description: str = ""
    state_schema: NonEmptyStr = Field(default="dict[str, Any]")

    inputs: tuple[WorkflowInputDefinition, ...] = Field(default_factory=tuple)
    steps: tuple[StepDefinition, ...] = Field(min_length=1)
    metadata: Mapping[str, Any] = Field(default_factory=lambda: freeze_value({}))

    @field_validator("version")
    @classmethod
    def validate_semver(cls, v: str) -> str:
        # Rejects surrounding whitespace and non-SemVer strings
        if not SEMVER_REGEX.fullmatch(v):
            raise ValueError(
                f"Version '{v}' is not a valid Semantic Version 2.0.0 (surrounding whitespace and leading zeros are rejected)"
            )
        return v

    @field_validator("metadata", mode="after")
    @classmethod
    def validate_and_freeze_metadata(cls, v: Any) -> Any:
        validate_json_serializable(v)
        return freeze_value(v)

    @field_serializer("metadata")
    def serialize_metadata(self, v: Any) -> dict[str, Any]:
        return unfreeze_value(v)

    @model_validator(mode="after")
    def validate_definition_internal_integrity(self) -> "WorkflowDefinition":
        # Check uniqueness of workflow input keys
        input_keys = [inp.key for inp in self.inputs]
        if len(input_keys) != len(set(input_keys)):
            raise ValueError("Workflow inputs contain duplicate keys")

        # Check uniqueness of step IDs
        step_ids = [s.step_id for s in self.steps]
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("Workflow steps contain duplicate step_ids")

        return self

    def compute_hash(self) -> Sha256Hash:
        """Compute deterministic canonical SHA-256 hash across all semantic fields."""
        return compute_canonical_workflow_hash(self)
