"""Declarative StepDefinition model and Capability contract."""

import re
from typing import Annotated, Any, Mapping
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_serializer, field_validator, model_validator

from src.core.contracts.ids import NonEmptyStr, StepId
from src.core.workflow.freezer import freeze_value, unfreeze_value, validate_json_serializable

# Capability format: lowercase dot-separated identifier, e.g. "requirement.analysis"
CAPABILITY_REGEX = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")
Capability = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")
]


class StepDefinition(BaseModel):
    """
    Declarative specification of a single logical unit of work in a workflow.

    Agent-Agnostic: Defines WHAT capability is required, not WHICH agent executes it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    step_id: StepId
    name: NonEmptyStr
    capability: Capability
    description: str = ""

    depends_on: tuple[StepId, ...] = Field(default_factory=tuple)
    inputs: tuple[NonEmptyStr, ...] = Field(default_factory=tuple)
    outputs: tuple[NonEmptyStr, ...] = Field(default_factory=tuple)
    config: Mapping[str, Any] = Field(default_factory=lambda: freeze_value({}))

    @field_validator("config", mode="after")
    @classmethod
    def validate_and_freeze_config(cls, v: Any) -> Any:
        validate_json_serializable(v)
        return freeze_value(v)

    @field_serializer("config")
    def serialize_config(self, v: Any) -> dict[str, Any]:
        return unfreeze_value(v)

    @model_validator(mode="after")
    def validate_step_internal_integrity(self) -> "StepDefinition":
        # Reject self-dependency
        if self.step_id in self.depends_on:
            raise ValueError(f"Step '{self.step_id}' cannot depend on itself")

        # Reject duplicate dependencies within the same step
        if len(self.depends_on) != len(set(self.depends_on)):
            raise ValueError(f"Step '{self.step_id}' has duplicate depends_on entries: {self.depends_on}")

        # Reject duplicate input and output declarations within the same step
        if len(self.inputs) != len(set(self.inputs)):
            raise ValueError(f"Step '{self.step_id}' has duplicate inputs declarations: {self.inputs}")
        if len(self.outputs) != len(set(self.outputs)):
            raise ValueError(f"Step '{self.step_id}' has duplicate outputs declarations: {self.outputs}")

        return self
