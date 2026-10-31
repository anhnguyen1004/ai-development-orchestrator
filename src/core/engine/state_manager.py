"""StateManager for Copy-on-Write state management, snapshot validation, and output contract enforcement."""

import copy
from typing import Any
from src.core.contracts.artifact import ArtifactRef
from src.core.contracts.status import StepOutcome
from src.core.contracts.step import StepResult
from src.core.engine.errors import ApprovalContractError, StateValidationError
from src.core.workflow.freezer import validate_json_serializable
from src.core.workflow.state import WorkflowState
from src.core.workflow.step_definition import StepDefinition


class StateManager:
    """
    Manages Copy-on-Write workflow state updates and strictly validates step execution results.

    Enforces that:
    1. StepResult.current_state is a complete snapshot.
    2. Pre-existing keys cannot be deleted or modified.
    3. Output keys produced by a SUCCEEDED step must exactly match StepDefinition.outputs.
    4. Outputs must be strictly JSON-serializable.
    5. Approval-required steps must declare exactly one output.
    6. Candidate state from FAILED or WAITING_FOR_APPROVAL steps is never committed to WorkflowState.
    """

    @classmethod
    def validate_and_commit(
        cls,
        pre_state: WorkflowState[dict[str, Any]],
        result: StepResult[dict[str, Any]],
        step_def: StepDefinition,
    ) -> WorkflowState[dict[str, Any]]:
        """
        Validate the complete returned state snapshot and commit state delta if valid.

        Returns an updated WorkflowState on SUCCEEDED.
        Returns the original pre_state on SKIPPED, FAILED, or WAITING_FOR_APPROVAL.
        Raises StateValidationError or ApprovalContractError on contract breach.
        """
        # Create an isolated reference copy of pre-execution state for invariant checking
        pre_snapshot = copy.deepcopy(pre_state.data)

        if result.outcome == StepOutcome.SKIPPED:
            # State is unchanged when a step is skipped
            return pre_state

        if result.outcome == StepOutcome.FAILED:
            # Failed step outputs are quarantined and discarded; pre-state is preserved
            return pre_state

        if result.outcome == StepOutcome.WAITING_FOR_APPROVAL:
            # Approval steps must declare exactly one output in their definition
            if len(step_def.outputs) != 1:
                raise ApprovalContractError(
                    f"Approval-required step '{step_def.step_id}' must declare exactly 1 output, "
                    f"got {len(step_def.outputs)}"
                )
            # Candidate output is not committed to WorkflowState while approval is pending
            return pre_state

        if result.outcome == StepOutcome.SUCCEEDED:
            returned_state = result.current_state
            if not isinstance(returned_state, dict):
                raise StateValidationError(
                    f"Step '{step_def.step_id}' must return a dictionary as current_state, "
                    f"got {type(returned_state).__name__}"
                )

            # Invariant 1: Reject deletion of existing keys
            for k in pre_snapshot:
                if k not in returned_state:
                    raise StateValidationError(
                        f"Step '{step_def.step_id}' illegally deleted existing state key '{k}'"
                    )

            # Invariant 2: Reject modification of existing keys
            for k in pre_snapshot:
                if returned_state[k] != pre_snapshot[k]:
                    raise StateValidationError(
                        f"Step '{step_def.step_id}' illegally modified existing state key '{k}' "
                        f"(pre-execution value: {pre_snapshot[k]!r}, returned value: {returned_state[k]!r})"
                    )

            # Invariant 3: Exact declared outputs match
            returned_keys = set(returned_state.keys())
            pre_keys = set(pre_snapshot.keys())
            new_keys = returned_keys - pre_keys
            declared_outputs = set(str(out) for out in step_def.outputs)

            missing_outputs = declared_outputs - new_keys
            if missing_outputs:
                raise StateValidationError(
                    f"Step '{step_def.step_id}' succeeded but failed to produce declared "
                    f"output key(s): {sorted(missing_outputs)}"
                )

            undeclared_outputs = new_keys - declared_outputs
            if undeclared_outputs:
                raise StateValidationError(
                    f"Step '{step_def.step_id}' produced undeclared output key(s): "
                    f"{sorted(undeclared_outputs)}"
                )

            # Invariant 4: Validate JSON serializability of new outputs
            for k in new_keys:
                try:
                    validate_json_serializable(returned_state[k])
                except ValueError as err:
                    raise StateValidationError(
                        f"Step '{step_def.step_id}' produced non-JSON serializable output for key '{k}': {err}"
                    ) from err

            # Invariant 5: Construct updated validated state snapshot
            committed_data = dict(pre_snapshot)
            for k in new_keys:
                committed_data[k] = returned_state[k]

            return WorkflowState[dict[str, Any]](
                schema_id=pre_state.schema_id,
                data=committed_data,
            )

        raise StateValidationError(f"Unhandled StepOutcome: {result.outcome}")

    @classmethod
    def commit_approved_artifact(
        cls,
        pre_state: WorkflowState[dict[str, Any]],
        step_def: StepDefinition,
        artifact_ref: ArtifactRef,
    ) -> WorkflowState[dict[str, Any]]:
        """
        Commit an approved ArtifactRef into WorkflowState under the step's single declared output key.

        Enforces that the step declares exactly one output key and that the key does not collide
        with existing keys in WorkflowState.
        """
        if len(step_def.outputs) != 1:
            raise ApprovalContractError(
                f"Approval step '{step_def.step_id}' must declare exactly 1 output, "
                f"got {len(step_def.outputs)}"
            )

        output_key = str(step_def.outputs[0])
        if output_key in pre_state.data:
            raise StateValidationError(
                f"Cannot commit approved artifact: output key '{output_key}' already exists in workflow state"
            )

        committed_data = dict(pre_state.data)
        committed_data[output_key] = artifact_ref.model_dump()

        return WorkflowState[dict[str, Any]](
            schema_id=pre_state.schema_id,
            data=committed_data,
        )

