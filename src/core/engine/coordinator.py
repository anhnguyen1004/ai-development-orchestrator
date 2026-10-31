"""TransitionCoordinator: Atomic Checkpoint-First state transition and lifecycle coordination."""

import copy
from datetime import datetime, timezone
from typing import Any
from src.core.contracts.approval import ApprovalDecision, ApprovalRequest
from src.core.contracts.artifact import Sha256Hash
from src.core.contracts.errors import ExecutionError
from src.core.contracts.ids import NonEmptyStr, RunId, StepId, TaskId
from src.core.contracts.status import ApprovalStatus, TaskStatus, WorkflowStatus
from src.core.contracts.step import StepResult
from src.core.contracts.task import TaskRecord
from src.core.engine.checkpoint import CheckpointRecord, CheckpointStore
from src.core.engine.errors import (
    ApprovalContractError,
    CheckpointPersistError,
    CoordinatorConsistencyError,
    EventSinkError,
    IllegalStateTransitionError,
    WorkflowTerminatedError,
)
from src.core.engine.events import EventSink, EventType, ExecutionEvent
from src.core.engine.state_manager import StateManager
from src.core.workflow.state import WorkflowState
from src.core.workflow.step_definition import StepDefinition

VALID_WORKFLOW_TRANSITIONS: dict[WorkflowStatus, set[WorkflowStatus]] = {
    WorkflowStatus.PENDING: {WorkflowStatus.RUNNING, WorkflowStatus.CANCELLED},
    WorkflowStatus.RUNNING: {
        WorkflowStatus.WAITING_FOR_APPROVAL,
        WorkflowStatus.PAUSED,
        WorkflowStatus.SUSPENDED,
        WorkflowStatus.COMPLETED,
        WorkflowStatus.FAILED,
        WorkflowStatus.CANCELLED,
    },
    WorkflowStatus.WAITING_FOR_APPROVAL: {
        WorkflowStatus.RUNNING,
        WorkflowStatus.SUSPENDED,
        WorkflowStatus.CANCELLED,
    },
    WorkflowStatus.PAUSED: {WorkflowStatus.RUNNING, WorkflowStatus.CANCELLED},
    WorkflowStatus.SUSPENDED: {WorkflowStatus.RUNNING, WorkflowStatus.CANCELLED},
    WorkflowStatus.COMPLETED: set(),
    WorkflowStatus.FAILED: set(),
    WorkflowStatus.CANCELLED: set(),
}

VALID_TASK_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.PENDING: {TaskStatus.READY, TaskStatus.SKIPPED, TaskStatus.CANCELLED},
    TaskStatus.READY: {TaskStatus.RUNNING, TaskStatus.SKIPPED, TaskStatus.CANCELLED},
    TaskStatus.RUNNING: {
        TaskStatus.SUCCEEDED,
        TaskStatus.FAILED,
        TaskStatus.SKIPPED,
        TaskStatus.RETRYING,
        TaskStatus.CANCELLED,
    },
    TaskStatus.RETRYING: {TaskStatus.RUNNING, TaskStatus.CANCELLED},
    TaskStatus.SUCCEEDED: set(),
    TaskStatus.FAILED: set(),
    TaskStatus.SKIPPED: set(),
    TaskStatus.CANCELLED: set(),
}


class ActiveRunContext:
    """Internal mutable tracking envelope for an active workflow run."""

    def __init__(
        self,
        run_id: RunId,
        workflow_id: NonEmptyStr,
        definition_version: NonEmptyStr,
        definition_hash: Sha256Hash,
        initial_state: WorkflowState[dict[str, Any]],
        task_ids: tuple[StepId, ...] = (),
    ) -> None:
        self.run_id = run_id
        self.workflow_id = workflow_id
        self.definition_version = definition_version
        self.definition_hash = definition_hash
        self.workflow_status = WorkflowStatus.PENDING
        self.state = initial_state
        self.task_records: dict[StepId, TaskRecord] = {
            sid: TaskRecord(
                task_id=TaskId(f"task-{sid}"),
                run_id=run_id,
                status=TaskStatus.PENDING,
            )
            for sid in task_ids
        }
        self.pending_approval: ApprovalRequest | None = None
        self.execution_generations: dict[StepId, int] = {sid: 1 for sid in task_ids}
        self.metadata: dict[str, Any] = {}
        self.current_checkpoint_sequence: int = -1
        self.current_event_sequence: int = -1
        self.is_quarantined: bool = False


class TransitionCoordinator:
    """
    Coordinates atomic state transitions, event emission, checkpoint persistence, and quarantine.

    Guarantees:
    1. Checkpoint-First order with rollback on event recording failure.
    2. Zero mutation of active runtime context if persistence or event recording fails.
    3. Independent event and checkpoint sequence streams.
    4. Immediate quarantine on split-brain consistency failure (checkpoint rollback failure).
    """

    def __init__(
        self,
        checkpoint_store: CheckpointStore,
        event_sink: EventSink,
    ) -> None:
        self._checkpoint_store = checkpoint_store
        self._event_sink = event_sink
        self._runs: dict[str, ActiveRunContext] = {}

    def _get_run(self, run_id: RunId | str) -> ActiveRunContext:
        run_id_str = str(run_id)
        if run_id_str not in self._runs:
            raise KeyError(f"Run '{run_id_str}' is not registered with TransitionCoordinator")
        return self._runs[run_id_str]

    def _assert_not_quarantined(self, run_id: RunId | str) -> None:
        run = self._get_run(run_id)
        if run.is_quarantined:
            raise CoordinatorConsistencyError(
                f"Run '{run_id}' is quarantined due to previous consistency failure. "
                "All further transitions are strictly rejected."
            )

    def is_quarantined(self, run_id: RunId | str) -> bool:
        """Check whether a run is currently quarantined."""
        try:
            return self._get_run(run_id).is_quarantined
        except KeyError:
            return False

    def get_active_context(self, run_id: RunId | str) -> dict[str, Any]:
        """Return a copy of the active run context."""
        run = self._get_run(run_id)
        return {
            "run_id": str(run.run_id),
            "workflow_id": str(run.workflow_id),
            "definition_version": str(run.definition_version),
            "definition_hash": str(run.definition_hash),
            "workflow_status": run.workflow_status,
            "state": copy.deepcopy(run.state),
            "task_records": copy.deepcopy(run.task_records),
            "pending_approval": copy.deepcopy(run.pending_approval),
            "execution_generations": copy.deepcopy(run.execution_generations),
            "metadata": copy.deepcopy(run.metadata),
            "current_checkpoint_sequence": run.current_checkpoint_sequence,
            "current_event_sequence": run.current_event_sequence,
            "is_quarantined": run.is_quarantined,
        }

    async def _execute_commit(
        self,
        run: ActiveRunContext,
        candidate_workflow_status: WorkflowStatus,
        candidate_state: WorkflowState[dict[str, Any]],
        candidate_task_records: dict[StepId, Any],
        candidate_pending_approval: ApprovalRequest | None,
        candidate_execution_generations: dict[StepId, int],
        candidate_metadata: dict[str, Any],
        event_type: EventType,
        event_payload: dict[str, Any],
    ) -> CheckpointRecord:
        """Execute the atomic Checkpoint-First commit sequence with rollback on event failure."""
        next_checkpoint_seq = run.current_checkpoint_sequence + 1
        next_event_seq = run.current_event_sequence + 1

        candidate_checkpoint = CheckpointRecord(
            run_id=run.run_id,
            workflow_id=run.workflow_id,
            definition_version=run.definition_version,
            definition_hash=run.definition_hash,
            sequence_number=next_checkpoint_seq,
            workflow_status=candidate_workflow_status,
            state_snapshot=candidate_state,
            task_records=candidate_task_records,
            pending_approval=candidate_pending_approval,
            execution_generations=candidate_execution_generations,
            metadata=candidate_metadata,
        )

        candidate_event = ExecutionEvent(
            event_id=f"{run.run_id}-evt-{next_event_seq}",
            run_id=run.run_id,
            workflow_id=run.workflow_id,
            workflow_version=run.definition_version,
            definition_hash=run.definition_hash,
            sequence_number=next_event_seq,
            event_type=event_type,
            payload=event_payload,
        )

        # Step 1: Checkpoint save FIRST
        try:
            await self._checkpoint_store.save(candidate_checkpoint)
        except Exception as cp_err:
            # Active runtime state and sequences untouched
            if isinstance(cp_err, CheckpointPersistError):
                raise
            raise CheckpointPersistError(f"Failed to persist checkpoint: {cp_err}") from cp_err

        # Step 2: Event record SECOND
        try:
            await self._event_sink.record(candidate_event)
        except Exception as evt_err:
            # Event recording failed; rollback checkpoint immediately
            try:
                await self._checkpoint_store.rollback_latest(run.run_id, next_checkpoint_seq)
            except Exception as rollback_err:
                # CRITICAL: Checkpoint rollback failed! Split-brain detected -> Quarantine run
                run.is_quarantined = True
                raise CoordinatorConsistencyError(
                    f"CRITICAL: Event recording failed ({evt_err}) and checkpoint rollback failed "
                    f"({rollback_err}) for run '{run.run_id}' sequence {next_checkpoint_seq}. Run is quarantined."
                ) from rollback_err

            # Rollback succeeded; active runtime state remains untouched
            raise EventSinkError(f"Event recording failed: {evt_err}") from evt_err

        # Step 3: Publish candidate runtime state
        run.workflow_status = candidate_workflow_status
        run.state = candidate_state
        run.task_records = candidate_task_records
        run.pending_approval = candidate_pending_approval
        run.execution_generations = candidate_execution_generations
        run.metadata = candidate_metadata
        run.current_checkpoint_sequence = next_checkpoint_seq
        run.current_event_sequence = next_event_seq

        return candidate_checkpoint

    def _validate_workflow_transition(
        self,
        current_status: WorkflowStatus,
        target_status: WorkflowStatus,
    ) -> None:
        valid_targets = VALID_WORKFLOW_TRANSITIONS.get(current_status, set())
        if target_status not in valid_targets:
            raise IllegalStateTransitionError(
                f"Illegal workflow transition: cannot transition from '{current_status.value}' "
                f"to '{target_status.value}'"
            )

    def _validate_task_transition(
        self,
        step_id: StepId,
        current_status: TaskStatus,
        target_status: TaskStatus,
    ) -> None:
        valid_targets = VALID_TASK_TRANSITIONS.get(current_status, set())
        if target_status not in valid_targets:
            raise IllegalStateTransitionError(
                f"Illegal task transition for step '{step_id}': cannot transition from "
                f"'{current_status.value}' to '{target_status.value}'"
            )

    async def initialize_run(
        self,
        run_id: RunId,
        workflow_id: NonEmptyStr,
        definition_version: NonEmptyStr,
        definition_hash: Sha256Hash,
        initial_state: WorkflowState[dict[str, Any]],
        task_ids: tuple[StepId, ...] = (),
    ) -> CheckpointRecord:
        """Initialize a new workflow run in PENDING status at sequence 0."""
        run_id_str = str(run_id)
        if run_id_str in self._runs:
            existing = self._runs[run_id_str]
            if existing.is_quarantined:
                raise CoordinatorConsistencyError(
                    f"Run '{run_id}' is quarantined due to previous consistency failure. "
                    "All further transitions are strictly rejected."
                )
            raise IllegalStateTransitionError(
                f"Run '{run_id}' is already registered in coordinator (status: '{existing.workflow_status.value}')."
            )

        run = ActiveRunContext(
            run_id=run_id,
            workflow_id=workflow_id,
            definition_version=definition_version,
            definition_hash=definition_hash,
            initial_state=initial_state,
            task_ids=task_ids,
        )

        checkpoint = await self._execute_commit(
            run=run,
            candidate_workflow_status=WorkflowStatus.PENDING,
            candidate_state=initial_state,
            candidate_task_records=run.task_records,
            candidate_pending_approval=None,
            candidate_execution_generations=run.execution_generations,
            candidate_metadata=run.metadata,
            event_type=EventType.WORKFLOW_STARTED,
            event_payload={"workflow_id": str(workflow_id), "status": "PENDING"},
        )

        # Register in coordinator ONLY after commit successfully persists
        self._runs[run_id_str] = run
        return checkpoint

    async def start_run(self, run_id: RunId | str) -> CheckpointRecord:
        """Transition workflow from PENDING to RUNNING."""
        self._assert_not_quarantined(run_id)
        run = self._get_run(run_id)
        if run.workflow_status != WorkflowStatus.PENDING:
            if run.workflow_status in (WorkflowStatus.COMPLETED, WorkflowStatus.FAILED, WorkflowStatus.CANCELLED):
                raise WorkflowTerminatedError(
                    f"Cannot start run '{run_id}': workflow run is in terminal status '{run.workflow_status.value}'"
                )
            raise IllegalStateTransitionError(
                f"Cannot start run '{run_id}': workflow status is '{run.workflow_status.value}', "
                f"expected '{WorkflowStatus.PENDING.value}'"
            )
        self._validate_workflow_transition(run.workflow_status, WorkflowStatus.RUNNING)

        return await self._execute_commit(
            run=run,
            candidate_workflow_status=WorkflowStatus.RUNNING,
            candidate_state=run.state,
            candidate_task_records=run.task_records,
            candidate_pending_approval=run.pending_approval,
            candidate_execution_generations=run.execution_generations,
            candidate_metadata=run.metadata,
            event_type=EventType.WORKFLOW_STARTED,
            event_payload={"status": "RUNNING"},
        )

    async def mark_task_ready(self, run_id: RunId | str, step_id: StepId) -> CheckpointRecord:
        """Transition task from PENDING to READY."""
        self._assert_not_quarantined(run_id)
        run = self._get_run(run_id)
        task = run.task_records[step_id]
        self._validate_task_transition(step_id, task.status, TaskStatus.READY)

        cand_task_records = copy.deepcopy(run.task_records)
        cand_task_records[step_id] = cand_task_records[step_id].model_copy(
            update={"status": TaskStatus.READY}
        )

        return await self._execute_commit(
            run=run,
            candidate_workflow_status=run.workflow_status,
            candidate_state=run.state,
            candidate_task_records=cand_task_records,
            candidate_pending_approval=run.pending_approval,
            candidate_execution_generations=run.execution_generations,
            candidate_metadata=run.metadata,
            event_type=EventType.TASK_READY,
            event_payload={"step_id": str(step_id)},
        )

    async def mark_task_running(
        self,
        run_id: RunId | str,
        step_id: StepId,
        attempt_number: int = 1,
    ) -> CheckpointRecord:
        """Persist pre-dispatch checkpoint transitioning task to RUNNING."""
        self._assert_not_quarantined(run_id)
        run = self._get_run(run_id)
        task = run.task_records[step_id]
        self._validate_task_transition(step_id, task.status, TaskStatus.RUNNING)

        now = datetime.now(timezone.utc)
        cand_task_records = copy.deepcopy(run.task_records)
        cand_task_records[step_id] = cand_task_records[step_id].model_copy(
            update={"status": TaskStatus.RUNNING, "started_at": now}
        )

        return await self._execute_commit(
            run=run,
            candidate_workflow_status=run.workflow_status,
            candidate_state=run.state,
            candidate_task_records=cand_task_records,
            candidate_pending_approval=run.pending_approval,
            candidate_execution_generations=run.execution_generations,
            candidate_metadata=run.metadata,
            event_type=EventType.TASK_STARTED,
            event_payload={"step_id": str(step_id), "attempt_number": attempt_number},
        )

    async def commit_step_succeeded(
        self,
        run_id: RunId | str,
        step_id: StepId,
        result: StepResult[dict[str, Any]],
        step_def: StepDefinition,
    ) -> CheckpointRecord:
        """Validate returned state and commit task to SUCCEEDED."""
        self._assert_not_quarantined(run_id)
        run = self._get_run(run_id)
        task = run.task_records[step_id]
        self._validate_task_transition(step_id, task.status, TaskStatus.SUCCEEDED)

        new_state = StateManager.validate_and_commit(run.state, result, step_def)
        now = datetime.now(timezone.utc)

        cand_task_records = copy.deepcopy(run.task_records)
        cand_task_records[step_id] = cand_task_records[step_id].model_copy(
            update={"status": TaskStatus.SUCCEEDED, "completed_at": now}
        )

        return await self._execute_commit(
            run=run,
            candidate_workflow_status=run.workflow_status,
            candidate_state=new_state,
            candidate_task_records=cand_task_records,
            candidate_pending_approval=run.pending_approval,
            candidate_execution_generations=run.execution_generations,
            candidate_metadata=run.metadata,
            event_type=EventType.TASK_SUCCEEDED,
            event_payload={"step_id": str(step_id)},
        )

    async def commit_step_failed(
        self,
        run_id: RunId | str,
        step_id: StepId,
        error: ExecutionError,
        is_retry: bool = False,
        retry_delay: float | None = None,
    ) -> CheckpointRecord:
        """Commit task failure or retry transition."""
        self._assert_not_quarantined(run_id)
        run = self._get_run(run_id)
        task = run.task_records[step_id]

        cand_task_records = copy.deepcopy(run.task_records)
        if is_retry:
            self._validate_task_transition(step_id, task.status, TaskStatus.RETRYING)
            cand_task_records[step_id] = cand_task_records[step_id].model_copy(
                update={"status": TaskStatus.RETRYING}
            )
            return await self._execute_commit(
                run=run,
                candidate_workflow_status=run.workflow_status,
                candidate_state=run.state,
                candidate_task_records=cand_task_records,
                candidate_pending_approval=run.pending_approval,
                candidate_execution_generations=run.execution_generations,
                candidate_metadata=run.metadata,
                event_type=EventType.TASK_RETRY,
                event_payload={
                    "step_id": str(step_id),
                    "delay_seconds": retry_delay,
                    "error_category": error.category.value,
                },
            )
        else:
            self._validate_task_transition(step_id, task.status, TaskStatus.FAILED)
            self._validate_workflow_transition(run.workflow_status, WorkflowStatus.FAILED)
            now = datetime.now(timezone.utc)
            cand_task_records[step_id] = cand_task_records[step_id].model_copy(
                update={"status": TaskStatus.FAILED, "completed_at": now, "error": error}
            )
            return await self._execute_commit(
                run=run,
                candidate_workflow_status=WorkflowStatus.FAILED,
                candidate_state=run.state,
                candidate_task_records=cand_task_records,
                candidate_pending_approval=run.pending_approval,
                candidate_execution_generations=run.execution_generations,
                candidate_metadata=run.metadata,
                event_type=EventType.TASK_FAILED,
                event_payload={"step_id": str(step_id), "error_category": error.category.value},
            )

    async def commit_step_skipped(
        self,
        run_id: RunId | str,
        step_id: StepId,
        skip_reason: str,
    ) -> CheckpointRecord:
        """Commit task SKIPPED transition."""
        self._assert_not_quarantined(run_id)
        run = self._get_run(run_id)
        task = run.task_records[step_id]
        self._validate_task_transition(step_id, task.status, TaskStatus.SKIPPED)

        now = datetime.now(timezone.utc)
        cand_task_records = copy.deepcopy(run.task_records)
        cand_task_records[step_id] = cand_task_records[step_id].model_copy(
            update={"status": TaskStatus.SKIPPED, "completed_at": now}
        )

        return await self._execute_commit(
            run=run,
            candidate_workflow_status=run.workflow_status,
            candidate_state=run.state,
            candidate_task_records=cand_task_records,
            candidate_pending_approval=run.pending_approval,
            candidate_execution_generations=run.execution_generations,
            candidate_metadata=run.metadata,
            event_type=EventType.TASK_SKIPPED,
            event_payload={"step_id": str(step_id), "skip_reason": skip_reason},
        )

    async def suspend_for_approval(
        self,
        run_id: RunId | str,
        step_id: StepId,
        request: ApprovalRequest,
        step_def: StepDefinition,
    ) -> CheckpointRecord:
        """Suspend workflow awaiting approval decision on artifact."""
        self._assert_not_quarantined(run_id)
        run = self._get_run(run_id)
        self._validate_workflow_transition(run.workflow_status, WorkflowStatus.WAITING_FOR_APPROVAL)

        if step_id not in run.task_records:
            raise IllegalStateTransitionError(
                f"Cannot suspend for approval: step '{step_id}' is not a registered task in run '{run_id}'"
            )

        if len(step_def.outputs) != 1:
            raise ApprovalContractError(
                f"Approval step '{step_def.step_id}' must declare exactly 1 output, "
                f"got {len(step_def.outputs)}"
            )

        return await self._execute_commit(
            run=run,
            candidate_workflow_status=WorkflowStatus.WAITING_FOR_APPROVAL,
            candidate_state=run.state,
            candidate_task_records=run.task_records,
            candidate_pending_approval=request,
            candidate_execution_generations=run.execution_generations,
            candidate_metadata=run.metadata,
            event_type=EventType.APPROVAL_REQUESTED,
            event_payload={
                "step_id": str(step_id),
                "approval_id": str(request.approval_id),
                "artifact_id": str(request.artifact.artifact_id),
            },
        )

    async def commit_approval_outcome(
        self,
        run_id: RunId | str,
        step_id: StepId,
        status: ApprovalStatus,
        updated_state: WorkflowState[dict[str, Any]],
        decision: ApprovalDecision,
    ) -> CheckpointRecord:
        """Commit validated approval decision outcome."""
        self._assert_not_quarantined(run_id)
        run = self._get_run(run_id)

        # 1. Workflow must be in WAITING_FOR_APPROVAL
        if run.workflow_status in (WorkflowStatus.COMPLETED, WorkflowStatus.FAILED, WorkflowStatus.CANCELLED):
            raise WorkflowTerminatedError(
                f"Cannot commit approval outcome: workflow run is in terminal status '{run.workflow_status.value}'"
            )
        if run.workflow_status != WorkflowStatus.WAITING_FOR_APPROVAL:
            raise IllegalStateTransitionError(
                f"Cannot commit approval outcome: workflow status is '{run.workflow_status.value}', "
                f"expected '{WorkflowStatus.WAITING_FOR_APPROVAL.value}'"
            )

        # 2. Must have a pending approval
        if run.pending_approval is None:
            raise ApprovalContractError(
                f"Cannot commit approval outcome: run '{run_id}' has no pending approval request"
            )

        # 3. Decision approval_id must match pending approval_id
        if str(decision.approval_id) != str(run.pending_approval.approval_id):
            raise ApprovalContractError(
                f"approval_id mismatch: decision has '{decision.approval_id}', "
                f"pending request has '{run.pending_approval.approval_id}'"
            )

        # 4. step_id must match pending approval step_id
        if str(step_id) != str(run.pending_approval.step_id):
            raise ApprovalContractError(
                f"step_id mismatch: decision is for step '{step_id}', "
                f"pending request is for step '{run.pending_approval.step_id}'"
            )

        if step_id not in run.task_records:
            raise IllegalStateTransitionError(
                f"Step '{step_id}' not found in task records for run '{run_id}'"
            )

        now = datetime.now(timezone.utc)
        cand_task_records = copy.deepcopy(run.task_records)

        if status == ApprovalStatus.APPROVED:
            self._validate_workflow_transition(run.workflow_status, WorkflowStatus.RUNNING)
            self._validate_task_transition(step_id, cand_task_records[step_id].status, TaskStatus.SUCCEEDED)
            cand_task_records[step_id] = cand_task_records[step_id].model_copy(
                update={"status": TaskStatus.SUCCEEDED, "completed_at": now}
            )
            return await self._execute_commit(
                run=run,
                candidate_workflow_status=WorkflowStatus.RUNNING,
                candidate_state=updated_state,
                candidate_task_records=cand_task_records,
                candidate_pending_approval=None,
                candidate_execution_generations=run.execution_generations,
                candidate_metadata=run.metadata,
                event_type=EventType.APPROVAL_DECIDED,
                event_payload={
                    "step_id": str(step_id),
                    "approval_id": str(decision.approval_id),
                    "artifact_id": str(decision.artifact_id),
                    "artifact_hash_reviewed": str(decision.artifact_hash_reviewed),
                    "reviewer": str(decision.reviewer),
                    "action": "APPROVE",
                },
            )

        if status == ApprovalStatus.REJECTED:
            self._validate_workflow_transition(run.workflow_status, WorkflowStatus.CANCELLED)
            for sid, rec in cand_task_records.items():
                if rec.status in (
                    TaskStatus.PENDING,
                    TaskStatus.READY,
                    TaskStatus.RUNNING,
                    TaskStatus.RETRYING,
                ):
                    cand_task_records[sid] = rec.model_copy(
                        update={"status": TaskStatus.CANCELLED, "completed_at": now}
                    )
            return await self._execute_commit(
                run=run,
                candidate_workflow_status=WorkflowStatus.CANCELLED,
                candidate_state=run.state,
                candidate_task_records=cand_task_records,
                candidate_pending_approval=None,
                candidate_execution_generations=run.execution_generations,
                candidate_metadata=run.metadata,
                event_type=EventType.WORKFLOW_CANCELLED,
                event_payload={
                    "source": "REVIEWER",
                    "step_id": str(step_id),
                    "approval_id": str(decision.approval_id),
                    "artifact_id": str(decision.artifact_id),
                    "artifact_hash_reviewed": str(decision.artifact_hash_reviewed),
                    "reviewer": str(decision.reviewer),
                    "action": "REJECT",
                },
            )

        if status == ApprovalStatus.CHANGES_REQUESTED:
            self._validate_workflow_transition(run.workflow_status, WorkflowStatus.SUSPENDED)
            return await self._execute_commit(
                run=run,
                candidate_workflow_status=WorkflowStatus.SUSPENDED,
                candidate_state=run.state,
                candidate_task_records=cand_task_records,
                candidate_pending_approval=None,
                candidate_execution_generations=run.execution_generations,
                candidate_metadata=run.metadata,
                event_type=EventType.APPROVAL_DECIDED,
                event_payload={
                    "step_id": str(step_id),
                    "approval_id": str(decision.approval_id),
                    "artifact_id": str(decision.artifact_id),
                    "artifact_hash_reviewed": str(decision.artifact_hash_reviewed),
                    "reviewer": str(decision.reviewer),
                    "action": "REQUEST_CHANGES",
                },
            )

        raise ApprovalContractError(f"Unhandled approval outcome status: {status}")

    async def pause_run(self, run_id: RunId | str) -> CheckpointRecord:
        """Pause workflow execution between steps."""
        self._assert_not_quarantined(run_id)
        run = self._get_run(run_id)
        self._validate_workflow_transition(run.workflow_status, WorkflowStatus.PAUSED)

        return await self._execute_commit(
            run=run,
            candidate_workflow_status=WorkflowStatus.PAUSED,
            candidate_state=run.state,
            candidate_task_records=run.task_records,
            candidate_pending_approval=run.pending_approval,
            candidate_execution_generations=run.execution_generations,
            candidate_metadata=run.metadata,
            event_type=EventType.WORKFLOW_PAUSED,
            event_payload={"status": "PAUSED"},
        )

    async def resume_run(self, run_id: RunId | str) -> CheckpointRecord:
        """Resume workflow execution from PAUSED or SUSPENDED."""
        self._assert_not_quarantined(run_id)
        run = self._get_run(run_id)
        if run.workflow_status not in (WorkflowStatus.PAUSED, WorkflowStatus.SUSPENDED):
            if run.workflow_status in (WorkflowStatus.COMPLETED, WorkflowStatus.FAILED, WorkflowStatus.CANCELLED):
                raise WorkflowTerminatedError(
                    f"Cannot resume run '{run_id}': workflow run is in terminal status '{run.workflow_status.value}'"
                )
            raise IllegalStateTransitionError(
                f"Cannot resume run '{run_id}': workflow status is '{run.workflow_status.value}', "
                "expected 'PAUSED' or 'SUSPENDED'"
            )
        self._validate_workflow_transition(run.workflow_status, WorkflowStatus.RUNNING)

        return await self._execute_commit(
            run=run,
            candidate_workflow_status=WorkflowStatus.RUNNING,
            candidate_state=run.state,
            candidate_task_records=run.task_records,
            candidate_pending_approval=run.pending_approval,
            candidate_execution_generations=run.execution_generations,
            candidate_metadata=run.metadata,
            event_type=EventType.WORKFLOW_RESUMED,
            event_payload={"status": "RUNNING"},
        )

    async def cancel_run(
        self,
        run_id: RunId | str,
        source: str = "USER",
        reason: str = "User requested abort",
    ) -> CheckpointRecord:
        """Cancel workflow execution."""
        self._assert_not_quarantined(run_id)
        run = self._get_run(run_id)
        self._validate_workflow_transition(run.workflow_status, WorkflowStatus.CANCELLED)

        now = datetime.now(timezone.utc)
        cand_task_records = copy.deepcopy(run.task_records)
        for sid, rec in cand_task_records.items():
            if rec.status in (TaskStatus.PENDING, TaskStatus.READY, TaskStatus.RUNNING, TaskStatus.RETRYING):
                cand_task_records[sid] = rec.model_copy(
                    update={"status": TaskStatus.CANCELLED, "completed_at": now}
                )

        return await self._execute_commit(
            run=run,
            candidate_workflow_status=WorkflowStatus.CANCELLED,
            candidate_state=run.state,
            candidate_task_records=cand_task_records,
            candidate_pending_approval=run.pending_approval,
            candidate_execution_generations=run.execution_generations,
            candidate_metadata=run.metadata,
            event_type=EventType.WORKFLOW_CANCELLED,
            event_payload={"source": source, "reason": reason},
        )

    async def complete_run(self, run_id: RunId | str) -> CheckpointRecord:
        """Complete workflow execution."""
        self._assert_not_quarantined(run_id)
        run = self._get_run(run_id)
        self._validate_workflow_transition(run.workflow_status, WorkflowStatus.COMPLETED)

        return await self._execute_commit(
            run=run,
            candidate_workflow_status=WorkflowStatus.COMPLETED,
            candidate_state=run.state,
            candidate_task_records=run.task_records,
            candidate_pending_approval=run.pending_approval,
            candidate_execution_generations=run.execution_generations,
            candidate_metadata=run.metadata,
            event_type=EventType.WORKFLOW_COMPLETED,
            event_payload={"status": "COMPLETED"},
        )

    async def record_event_only(
        self,
        run_id: RunId | str,
        event_type: EventType,
        payload: dict[str, Any],
    ) -> ExecutionEvent:
        """Record an independent event without advancing the checkpoint sequence."""
        self._assert_not_quarantined(run_id)
        run = self._get_run(run_id)

        next_event_seq = run.current_event_sequence + 1
        event = ExecutionEvent(
            event_id=f"{run.run_id}-evt-{next_event_seq}",
            run_id=run.run_id,
            workflow_id=run.workflow_id,
            workflow_version=run.definition_version,
            definition_hash=run.definition_hash,
            sequence_number=next_event_seq,
            event_type=event_type,
            payload=payload,
        )

        try:
            await self._event_sink.record(event)
        except Exception as e:
            raise EventSinkError(f"Failed to record event: {e}") from e

        run.current_event_sequence = next_event_seq
        return event

    def restore_from_checkpoint(self, checkpoint: CheckpointRecord) -> None:
        """Restore in-memory ActiveRunContext from a loaded CheckpointRecord."""
        run = ActiveRunContext(
            run_id=checkpoint.run_id,
            workflow_id=checkpoint.workflow_id,
            definition_version=checkpoint.definition_version,
            definition_hash=checkpoint.definition_hash,
            initial_state=checkpoint.state_snapshot,
        )
        run.workflow_status = checkpoint.workflow_status
        run.task_records = dict(checkpoint.task_records)
        run.pending_approval = checkpoint.pending_approval
        run.execution_generations = dict(checkpoint.execution_generations)
        run.metadata = dict(checkpoint.metadata)
        run.current_checkpoint_sequence = checkpoint.sequence_number
        # Event sequence is restored to at least checkpoint sequence
        run.current_event_sequence = checkpoint.sequence_number
        run.is_quarantined = False

        self._runs[str(checkpoint.run_id)] = run

