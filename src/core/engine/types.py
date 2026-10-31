"""Runtime types, read-only state view, execution context, and artifact store protocol."""

from collections.abc import Iterator, Mapping
import hashlib
import types
from typing import Any, Protocol, runtime_checkable
from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

from src.core.contracts.artifact import ArtifactRef
from src.core.contracts.ids import NonEmptyStr, RunId, StepId
from src.core.engine.errors import ArtifactStorageVerificationError
from src.core.workflow.freezer import freeze_value, unfreeze_value, validate_json_serializable
from src.core.workflow.state import WorkflowState
from src.core.workflow.step_definition import Capability


class ReadOnlyStateView(Mapping[str, Any]):
    """
    Read-only view over WorkflowState protecting top-level and nested state from mutation.

    Implements collections.abc.Mapping to provide standard dictionary-like read access while
    preventing all in-place mutations.
    """

    def __init__(
        self,
        state: WorkflowState[dict[str, Any]] | dict[str, Any],
        schema_id: str = "dict[str, Any]",
    ) -> None:
        if isinstance(state, WorkflowState):
            self._schema_id = str(state.schema_id)
            raw_data = state.data
        else:
            self._schema_id = schema_id
            raw_data = state

        validate_json_serializable(raw_data)
        frozen = freeze_value(raw_data)
        if not isinstance(frozen, types.MappingProxyType):
            frozen = types.MappingProxyType(frozen if isinstance(frozen, dict) else {})
        self._data: types.MappingProxyType[str, Any] = frozen

    @property
    def schema_id(self) -> str:
        """Return the schema identifier of the state snapshot."""
        return self._schema_id

    @property
    def data(self) -> types.MappingProxyType[str, Any]:
        """Return the deeply frozen MappingProxyType view."""
        return self._data

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def to_dict(self) -> dict[str, Any]:
        """Produce an independent, unfreezed, mutable copy of the underlying state dictionary."""
        return unfreeze_value(self._data)

    def __repr__(self) -> str:
        return f"ReadOnlyStateView(schema_id={self._schema_id!r}, data={dict(self._data)!r})"

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, ReadOnlyStateView):
            return self._schema_id == other._schema_id and self._data == other._data
        if isinstance(other, (dict, Mapping)):
            return self._data == other
        return False


def compute_executor_idempotency_key(
    run_id: str,
    step_id: str,
    execution_generation: int = 1,
) -> str:
    """Compute deterministic SHA-256 idempotency key for step execution attempt."""
    payload = f"{run_id}:{step_id}:{execution_generation}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class StepExecutionContext(BaseModel):
    """
    Immutable runtime context and scoped parameters provided to a StepExecutor.

    Ensures executors receive deeply frozen inputs, frozen configuration, and a read-only
    state view preventing direct in-place mutation of workflow state.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    run_id: RunId
    step_id: StepId
    step_name: NonEmptyStr
    capability: Capability
    inputs: Mapping[str, Any] = Field(default_factory=lambda: freeze_value({}))
    config: Mapping[str, Any] = Field(default_factory=lambda: freeze_value({}))
    attempt_number: int = Field(ge=1, description="1-based retry attempt number")
    executor_idempotency_key: str = Field(description="Deterministic attempt deduplication key")
    current_state: ReadOnlyStateView = Field(description="Read-only pre-execution state view")

    @field_validator("inputs", mode="after")
    @classmethod
    def validate_and_freeze_inputs(cls, v: Any) -> Mapping[str, Any]:
        validate_json_serializable(v)
        frozen = freeze_value(v)
        if not isinstance(frozen, Mapping):
            raise ValueError("inputs must be a mapping")
        return frozen

    @field_validator("config", mode="after")
    @classmethod
    def validate_and_freeze_config(cls, v: Any) -> Mapping[str, Any]:
        validate_json_serializable(v)
        frozen = freeze_value(v)
        if not isinstance(frozen, Mapping):
            raise ValueError("config must be a mapping")
        return frozen

    @field_serializer("inputs")
    def serialize_inputs(self, v: Mapping[str, Any]) -> dict[str, Any]:
        return unfreeze_value(v)

    @field_serializer("config")
    def serialize_config(self, v: Mapping[str, Any]) -> dict[str, Any]:
        return unfreeze_value(v)

    @field_serializer("current_state")
    def serialize_current_state(self, v: ReadOnlyStateView) -> dict[str, Any]:
        return {"schema_id": v.schema_id, "data": v.to_dict()}


@runtime_checkable
class ArtifactStore(Protocol):
    """Abstract protocol for inspecting and verifying stored artifacts."""

    async def verify_artifact(self, ref: ArtifactRef) -> bool:
        """
        Verify that an artifact reference matches actual stored data.

        Returns True if the artifact exists and its version and content hash match.
        Raises ArtifactStorageVerificationError if missing, corrupted, or hash mismatch.
        """
        ...


class InMemoryArtifactStore:
    """In-memory reference store for artifact verification."""

    def __init__(self) -> None:
        self._artifacts: dict[tuple[str, int], tuple[str, Any]] = {}

    def put_artifact(
        self,
        artifact_id: str,
        version: int,
        content_hash: str,
        payload: Any = None,
    ) -> None:
        """Store an artifact reference and content hash in memory."""
        self._artifacts[(str(artifact_id), int(version))] = (
            str(content_hash).strip().lower(),
            payload,
        )

    async def verify_artifact(self, ref: ArtifactRef) -> bool:
        """
        Verify that the referenced artifact exists and its hash matches.

        Raises ArtifactStorageVerificationError if missing or hash mismatch.
        """
        key = (str(ref.artifact_id), int(ref.version))
        if key not in self._artifacts:
            raise ArtifactStorageVerificationError(
                f"Artifact '{ref.artifact_id}' version {ref.version} not found in storage"
            )
        stored_hash, _ = self._artifacts[key]
        expected_hash = str(ref.content_hash).strip().lower()
        if stored_hash != expected_hash:
            raise ArtifactStorageVerificationError(
                f"Artifact '{ref.artifact_id}' version {ref.version} content hash mismatch: "
                f"expected '{expected_hash}', storage has '{stored_hash}'"
            )
        return True
