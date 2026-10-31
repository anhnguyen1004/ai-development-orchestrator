"""Type-safe identifiers with compile-time distinction and runtime validation."""

from typing import Annotated, NewType
from pydantic import StringConstraints

# Base non-empty string constraint (strips leading/trailing whitespace, enforces min_length=1)
NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]

# Static NewType definitions for static type checking (Mypy/Pyright)
RunIdType = NewType("RunId", str)
TaskIdType = NewType("TaskId", str)
StepIdType = NewType("StepId", str)
ApprovalIdType = NewType("ApprovalId", str)
OperationIdType = NewType("OperationId", str)
ArtifactIdType = NewType("ArtifactId", str)

# Runtime-validated Pydantic field types
RunId = Annotated[RunIdType, StringConstraints(strip_whitespace=True, min_length=1)]
TaskId = Annotated[TaskIdType, StringConstraints(strip_whitespace=True, min_length=1)]
StepId = Annotated[StepIdType, StringConstraints(strip_whitespace=True, min_length=1)]
ApprovalId = Annotated[ApprovalIdType, StringConstraints(strip_whitespace=True, min_length=1)]
OperationId = Annotated[OperationIdType, StringConstraints(strip_whitespace=True, min_length=1)]
ArtifactId = Annotated[ArtifactIdType, StringConstraints(strip_whitespace=True, min_length=1)]

