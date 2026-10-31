"""Unit tests for TransitionCoordinator: Checkpoint-First state transitions, event emission, and quarantine."""

import copy
from datetime import datetime, timezone
from typing import Any, Coroutine
import pytest

from src.core.contracts.approval import ApprovalDecision, ApprovalRequest, DecisionAction
from src.core.contracts.artifact import ArtifactRef, Sha256Hash
from src.core.contracts.errors import ErrorCategory, ExecutionError
from src.core.contracts.ids import (
    ApprovalId,
    ArtifactId,
    NonEmptyStr,
    RunId,
    StepId,
    TaskId,
)
from src.core.contracts.status import ApprovalStatus, StepOutcome, TaskStatus, WorkflowStatus
from src.core.contracts.step import StepResult
from src.core.contracts.task import TaskRecord
from src.core.engine.checkpoint import (
    CheckpointRecord,
    InMemoryCheckpointStore,
)
from src.core.engine.coordinator import (
    TransitionCoordinator,
    VALID_TASK_TRANSITIONS,
    VALID_WORKFLOW_TRANSITIONS,
)
from src.core.engine.errors import (
    ApprovalContractError,
    CheckpointNotFoundError,
    CheckpointPersistError,
    CoordinatorConsistencyError,
    EventSinkError,
    IllegalStateTransitionError,
    StateValidationError,
    WorkflowTerminatedError,
)
from src.core.engine.events import EventType, ExecutionEvent, InMemoryEventSink
from src.core.workflow.state import WorkflowState
from src.core.workflow.step_definition import Capability, StepDefinition

DUMMY_HASH = Sha256Hash("b" * 64)


def run_coro(coro: Coroutine[Any, Any, Any]) -> Any:
    """Run a coroutine to completion synchronously without requiring network/socketpair."""
    try:
        coro.send(None)
    except StopIteration as e:
        return e.value


def create_step_def(
    step_id: str = "step-1",
    outputs: tuple[str, ...] = ("out_val",),
) -> StepDefinition:
    return StepDefinition(
        step_id=StepId(step_id),
        name=NonEmptyStr(f"Step {step_id}"),
        capability=Capability("test.capability"),
        outputs=tuple(NonEmptyStr(o) for o in outputs),
    )


class FaultyCheckpointStore(InMemoryCheckpointStore):
    def __init__(self, fail_save: bool = False, fail_rollback: bool = False) -> None:
        super().__init__()
        self.fail_save = fail_save
        self.fail_rollback = fail_rollback

    async def save(self, record: CheckpointRecord) -> None:
        if self.fail_save:
            raise RuntimeError("Injected checkpoint store disk failure")
        await super().save(record)

    async def rollback_latest(self, run_id: RunId | str, expected_sequence_number: int) -> None:
        if self.fail_rollback:
            raise RuntimeError("Injected rollback catastrophic failure")
        await super().rollback_latest(run_id, expected_sequence_number)


class FaultyEventSink(InMemoryEventSink):
    def __init__(self, fail_record: bool = False) -> None:
        super().__init__()
        self.fail_record = fail_record

    async def record(self, event: ExecutionEvent) -> None:
        if self.fail_record:
            raise RuntimeError("Injected event sink broker unreachable")
        await super().record(event)


class TestTransitionCoordinatorLifecycle:
    def test_full_successful_workflow_lifecycle(self):
        cp_store = InMemoryCheckpointStore()
        evt_sink = InMemoryEventSink()
        coordinator = TransitionCoordinator(cp_store, evt_sink)

        run_id = RunId("run-100")
        step_1 = StepId("step-1")
        initial_state = WorkflowState[dict[str, Any]](data={"base_count": 1})
        step_def = create_step_def("step-1", outputs=("res_val",))

        # 1. Initialize
        cp0 = run_coro(
            coordinator.initialize_run(
                run_id=run_id,
                workflow_id=NonEmptyStr("wf-100"),
                definition_version=NonEmptyStr("1.0.0"),
                definition_hash=DUMMY_HASH,
                initial_state=initial_state,
                task_ids=(step_1,),
            )
        )
        assert cp0.sequence_number == 0
        assert cp0.workflow_status == WorkflowStatus.PENDING
        assert cp0.task_records[step_1].status == TaskStatus.PENDING
        assert len(evt_sink.get_events()) == 1
        assert evt_sink.get_events()[0].event_type == EventType.WORKFLOW_STARTED

        # 2. Start run
        cp1 = run_coro(coordinator.start_run(run_id))
        assert cp1.sequence_number == 1
        assert cp1.workflow_status == WorkflowStatus.RUNNING
        assert len(evt_sink.get_events()) == 2
        assert evt_sink.get_events()[1].event_type == EventType.WORKFLOW_STARTED
        assert evt_sink.get_events()[1].payload["status"] == "RUNNING"

        # 3. Mark task READY
        cp2 = run_coro(coordinator.mark_task_ready(run_id, step_1))
        assert cp2.sequence_number == 2
        assert cp2.task_records[step_1].status == TaskStatus.READY
        assert evt_sink.get_events()[2].event_type == EventType.TASK_READY

        # 4. Mark task RUNNING
        cp3 = run_coro(coordinator.mark_task_running(run_id, step_1, attempt_number=1))
        assert cp3.sequence_number == 3
        assert cp3.task_records[step_1].status == TaskStatus.RUNNING
        assert cp3.task_records[step_1].started_at is not None
        assert evt_sink.get_events()[3].event_type == EventType.TASK_STARTED

        # 5. Commit task SUCCEEDED with state output
        step_result = StepResult[dict[str, Any]](
            step_id=step_1,
            outcome=StepOutcome.SUCCEEDED,
            current_state={"base_count": 1, "res_val": "success_output"},
        )
        cp4 = run_coro(coordinator.commit_step_succeeded(run_id, step_1, step_result, step_def))
        assert cp4.sequence_number == 4
        assert cp4.task_records[step_1].status == TaskStatus.SUCCEEDED
        assert cp4.task_records[step_1].completed_at is not None
        assert cp4.state_snapshot.data["res_val"] == "success_output"
        assert evt_sink.get_events()[4].event_type == EventType.TASK_SUCCEEDED

        # 6. Complete workflow run
        cp5 = run_coro(coordinator.complete_run(run_id))
        assert cp5.sequence_number == 5
        assert cp5.workflow_status == WorkflowStatus.COMPLETED
        assert evt_sink.get_events()[5].event_type == EventType.WORKFLOW_COMPLETED

        # Verify active context
        ctx = coordinator.get_active_context(run_id)
        assert ctx["workflow_status"] == WorkflowStatus.COMPLETED
        assert ctx["current_checkpoint_sequence"] == 5
        assert ctx["current_event_sequence"] == 5

    def test_step_failure_and_retry_flow(self):
        cp_store = InMemoryCheckpointStore()
        evt_sink = InMemoryEventSink()
        coordinator = TransitionCoordinator(cp_store, evt_sink)

        run_id = RunId("run-retry")
        step_1 = StepId("step-1")
        initial_state = WorkflowState[dict[str, Any]](data={"val": 1})

        run_coro(coordinator.initialize_run(
            run_id=run_id,
            workflow_id=NonEmptyStr("wf-retry"),
            definition_version=NonEmptyStr("1.0.0"),
            definition_hash=DUMMY_HASH,
            initial_state=initial_state,
            task_ids=(step_1,),
        ))
        run_coro(coordinator.start_run(run_id))
        run_coro(coordinator.mark_task_ready(run_id, step_1))
        run_coro(coordinator.mark_task_running(run_id, step_1, attempt_number=1))

        # Retry step failure
        retry_err = ExecutionError(
            category=ErrorCategory.TRANSIENT,
            message=NonEmptyStr("Temporary network glitch"),
        )
        cp_retry = run_coro(
            coordinator.commit_step_failed(
                run_id=run_id,
                step_id=step_1,
                error=retry_err,
                is_retry=True,
                retry_delay=1.5,
            )
        )
        assert cp_retry.workflow_status == WorkflowStatus.RUNNING  # Workflow stays RUNNING
        assert cp_retry.task_records[step_1].status == TaskStatus.RETRYING

        # Re-dispatch from RETRYING -> RUNNING
        cp_running2 = run_coro(coordinator.mark_task_running(run_id, step_1, attempt_number=2))
        assert cp_running2.task_records[step_1].status == TaskStatus.RUNNING

        # Fatal step failure (exhausted retries)
        fatal_err = ExecutionError(
            category=ErrorCategory.PERMANENT,
            message=NonEmptyStr("Fatal unrecoverable error"),
        )
        cp_fatal = run_coro(
            coordinator.commit_step_failed(
                run_id=run_id,
                step_id=step_1,
                error=fatal_err,
                is_retry=False,
            )
        )
        assert cp_fatal.task_records[step_1].status == TaskStatus.FAILED
        assert cp_fatal.task_records[step_1].error is not None
        assert cp_fatal.workflow_status == WorkflowStatus.FAILED

    def test_step_skipped_flow(self):
        cp_store = InMemoryCheckpointStore()
        evt_sink = InMemoryEventSink()
        coordinator = TransitionCoordinator(cp_store, evt_sink)

        run_id = RunId("run-skip")
        step_1 = StepId("step-1")
        initial_state = WorkflowState[dict[str, Any]](data={"val": 1})

        run_coro(coordinator.initialize_run(
            run_id=run_id,
            workflow_id=NonEmptyStr("wf-skip"),
            definition_version=NonEmptyStr("1.0.0"),
            definition_hash=DUMMY_HASH,
            initial_state=initial_state,
            task_ids=(step_1,),
        ))
        run_coro(coordinator.start_run(run_id))
        run_coro(coordinator.mark_task_ready(run_id, step_1))

        # Skip from READY
        cp_skip = run_coro(
            coordinator.commit_step_skipped(
                run_id=run_id,
                step_id=step_1,
                skip_reason="Condition expression evaluated to false",
            )
        )
        assert cp_skip.task_records[step_1].status == TaskStatus.SKIPPED
        assert cp_skip.task_records[step_1].completed_at is not None
        assert evt_sink.get_events()[-1].event_type == EventType.TASK_SKIPPED
        assert evt_sink.get_events()[-1].payload["skip_reason"] == "Condition expression evaluated to false"


class TestTransitionCoordinatorApprovalFlow:
    def test_approval_suspend_and_approved_decision(self):
        cp_store = InMemoryCheckpointStore()
        evt_sink = InMemoryEventSink()
        coordinator = TransitionCoordinator(cp_store, evt_sink)

        run_id = RunId("run-app")
        step_review = StepId("step-review")
        initial_state = WorkflowState[dict[str, Any]](data={"doc_title": "Plan"})
        step_def = create_step_def("step-review", outputs=("approved_artifact",))

        run_coro(coordinator.initialize_run(
            run_id=run_id,
            workflow_id=NonEmptyStr("wf-app"),
            definition_version=NonEmptyStr("1.0.0"),
            definition_hash=DUMMY_HASH,
            initial_state=initial_state,
            task_ids=(step_review,),
        ))
        run_coro(coordinator.start_run(run_id))
        run_coro(coordinator.mark_task_ready(run_id, step_review))
        run_coro(coordinator.mark_task_running(run_id, step_review))

        # Suspend for approval
        app_req = ApprovalRequest(
            approval_id=ApprovalId("app-001"),
            run_id=run_id,
            step_id=step_review,
            artifact=ArtifactRef(
                artifact_id=ArtifactId("art-spec"),
                content_hash=DUMMY_HASH,
                version=1,
            ),
            summary=NonEmptyStr("Please review specification"),
            status=ApprovalStatus.PENDING,
        )
        cp_suspend = run_coro(
            coordinator.suspend_for_approval(run_id, step_review, app_req, step_def)
        )
        assert cp_suspend.workflow_status == WorkflowStatus.WAITING_FOR_APPROVAL
        assert cp_suspend.pending_approval is not None
        assert str(cp_suspend.pending_approval.approval_id) == "app-001"

        # Commit APPROVED outcome
        decision = ApprovalDecision(
            approval_id=ApprovalId("app-001"),
            run_id=run_id,
            step_id=step_review,
            action=DecisionAction.APPROVE,
            reviewer=NonEmptyStr("alice@company.com"),
            artifact_id=ArtifactId("art-spec"),
            artifact_version=1,
            artifact_hash_reviewed=DUMMY_HASH,
        )
        updated_state = WorkflowState[dict[str, Any]](
            data={"doc_title": "Plan", "approved_artifact": {"artifact_id": "art-spec"}}
        )
        cp_approved = run_coro(
            coordinator.commit_approval_outcome(
                run_id=run_id,
                step_id=step_review,
                status=ApprovalStatus.APPROVED,
                updated_state=updated_state,
                decision=decision,
            )
        )
        assert cp_approved.workflow_status == WorkflowStatus.RUNNING
        assert cp_approved.pending_approval is None
        assert cp_approved.task_records[step_review].status == TaskStatus.SUCCEEDED
        assert cp_approved.state_snapshot.data["approved_artifact"]["artifact_id"] == "art-spec"

    def test_approval_step_def_must_declare_exactly_one_output(self):
        cp_store = InMemoryCheckpointStore()
        evt_sink = InMemoryEventSink()
        coordinator = TransitionCoordinator(cp_store, evt_sink)

        run_id = RunId("run-app-err")
        step_review = StepId("step-review")
        initial_state = WorkflowState[dict[str, Any]](data={})
        step_def_invalid = create_step_def("step-review", outputs=("out1", "out2"))

        run_coro(coordinator.initialize_run(
            run_id=run_id,
            workflow_id=NonEmptyStr("wf-app-err"),
            definition_version=NonEmptyStr("1.0.0"),
            definition_hash=DUMMY_HASH,
            initial_state=initial_state,
            task_ids=(step_review,),
        ))
        run_coro(coordinator.start_run(run_id))

        app_req = ApprovalRequest(
            approval_id=ApprovalId("app-002"),
            run_id=run_id,
            step_id=step_review,
            artifact=ArtifactRef(
                artifact_id=ArtifactId("art-1"),
                content_hash=DUMMY_HASH,
                version=1,
            ),
            summary=NonEmptyStr("Review"),
        )
        with pytest.raises(ApprovalContractError, match="must declare exactly 1 output, got 2"):
            run_coro(coordinator.suspend_for_approval(run_id, step_review, app_req, step_def_invalid))

    def test_approval_rejection_cancels_workflow(self):
        cp_store = InMemoryCheckpointStore()
        evt_sink = InMemoryEventSink()
        coordinator = TransitionCoordinator(cp_store, evt_sink)

        run_id = RunId("run-reject")
        step_review = StepId("step-review")
        initial_state = WorkflowState[dict[str, Any]](data={})
        step_def = create_step_def("step-review", outputs=("out",))

        run_coro(coordinator.initialize_run(
            run_id=run_id,
            workflow_id=NonEmptyStr("wf-reject"),
            definition_version=NonEmptyStr("1.0.0"),
            definition_hash=DUMMY_HASH,
            initial_state=initial_state,
            task_ids=(step_review,),
        ))
        run_coro(coordinator.start_run(run_id))
        run_coro(coordinator.mark_task_ready(run_id, step_review))
        run_coro(coordinator.mark_task_running(run_id, step_review))

        app_req = ApprovalRequest(
            approval_id=ApprovalId("app-rej"),
            run_id=run_id,
            step_id=step_review,
            artifact=ArtifactRef(
                artifact_id=ArtifactId("art-1"),
                content_hash=DUMMY_HASH,
                version=1,
            ),
            summary=NonEmptyStr("Review"),
        )
        run_coro(coordinator.suspend_for_approval(run_id, step_review, app_req, step_def))

        decision = ApprovalDecision(
            approval_id=ApprovalId("app-rej"),
            run_id=run_id,
            step_id=step_review,
            action=DecisionAction.REJECT,
            reviewer=NonEmptyStr("bob@company.com"),
            artifact_id=ArtifactId("art-1"),
            artifact_version=1,
            artifact_hash_reviewed=DUMMY_HASH,
        )
        cp_rej = run_coro(
            coordinator.commit_approval_outcome(
                run_id=run_id,
                step_id=step_review,
                status=ApprovalStatus.REJECTED,
                updated_state=initial_state,
                decision=decision,
            )
        )
        assert cp_rej.workflow_status == WorkflowStatus.CANCELLED
        assert cp_rej.task_records[step_review].status == TaskStatus.CANCELLED
        assert cp_rej.pending_approval is None

    def test_approval_changes_requested_suspends_workflow(self):
        cp_store = InMemoryCheckpointStore()
        evt_sink = InMemoryEventSink()
        coordinator = TransitionCoordinator(cp_store, evt_sink)

        run_id = RunId("run-cr")
        step_review = StepId("step-review")
        initial_state = WorkflowState[dict[str, Any]](data={})
        step_def = create_step_def("step-review", outputs=("out",))

        run_coro(coordinator.initialize_run(
            run_id=run_id,
            workflow_id=NonEmptyStr("wf-cr"),
            definition_version=NonEmptyStr("1.0.0"),
            definition_hash=DUMMY_HASH,
            initial_state=initial_state,
            task_ids=(step_review,),
        ))
        run_coro(coordinator.start_run(run_id))
        run_coro(coordinator.mark_task_ready(run_id, step_review))
        run_coro(coordinator.mark_task_running(run_id, step_review))

        app_req = ApprovalRequest(
            approval_id=ApprovalId("app-cr"),
            run_id=run_id,
            step_id=step_review,
            artifact=ArtifactRef(
                artifact_id=ArtifactId("art-1"),
                content_hash=DUMMY_HASH,
                version=1,
            ),
            summary=NonEmptyStr("Review"),
        )
        run_coro(coordinator.suspend_for_approval(run_id, step_review, app_req, step_def))

        decision = ApprovalDecision(
            approval_id=ApprovalId("app-cr"),
            run_id=run_id,
            step_id=step_review,
            action=DecisionAction.REQUEST_CHANGES,
            reviewer=NonEmptyStr("charlie@company.com"),
            artifact_id=ArtifactId("art-1"),
            artifact_version=1,
            artifact_hash_reviewed=DUMMY_HASH,
        )
        cp_cr = run_coro(
            coordinator.commit_approval_outcome(
                run_id=run_id,
                step_id=step_review,
                status=ApprovalStatus.CHANGES_REQUESTED,
                updated_state=initial_state,
                decision=decision,
            )
        )
        assert cp_cr.workflow_status == WorkflowStatus.SUSPENDED
        assert cp_cr.pending_approval is None

        # Resume from SUSPENDED
        cp_resumed = run_coro(coordinator.resume_run(run_id))
        assert cp_resumed.workflow_status == WorkflowStatus.RUNNING


class TestTransitionCoordinatorPauseResumeCancel:
    def test_pause_and_resume_run(self):
        cp_store = InMemoryCheckpointStore()
        evt_sink = InMemoryEventSink()
        coordinator = TransitionCoordinator(cp_store, evt_sink)

        run_id = RunId("run-pause")
        initial_state = WorkflowState[dict[str, Any]](data={})
        run_coro(coordinator.initialize_run(
            run_id=run_id,
            workflow_id=NonEmptyStr("wf-pause"),
            definition_version=NonEmptyStr("1.0.0"),
            definition_hash=DUMMY_HASH,
            initial_state=initial_state,
        ))
        run_coro(coordinator.start_run(run_id))

        # Pause
        cp_paused = run_coro(coordinator.pause_run(run_id))
        assert cp_paused.workflow_status == WorkflowStatus.PAUSED
        assert evt_sink.get_events()[-1].event_type == EventType.WORKFLOW_PAUSED

        # Resume
        cp_resumed = run_coro(coordinator.resume_run(run_id))
        assert cp_resumed.workflow_status == WorkflowStatus.RUNNING
        assert evt_sink.get_events()[-1].event_type == EventType.WORKFLOW_RESUMED

    def test_cancel_run_cancels_all_active_tasks(self):
        cp_store = InMemoryCheckpointStore()
        evt_sink = InMemoryEventSink()
        coordinator = TransitionCoordinator(cp_store, evt_sink)

        run_id = RunId("run-cancel")
        step_1 = StepId("step-1")
        step_2 = StepId("step-2")
        initial_state = WorkflowState[dict[str, Any]](data={})
        run_coro(coordinator.initialize_run(
            run_id=run_id,
            workflow_id=NonEmptyStr("wf-cancel"),
            definition_version=NonEmptyStr("1.0.0"),
            definition_hash=DUMMY_HASH,
            initial_state=initial_state,
            task_ids=(step_1, step_2),
        ))
        run_coro(coordinator.start_run(run_id))
        run_coro(coordinator.mark_task_ready(run_id, step_1))
        run_coro(coordinator.mark_task_running(run_id, step_1))
        # step_2 remains PENDING

        cp_cancel = run_coro(
            coordinator.cancel_run(run_id, source="USER", reason="Aborted by operator")
        )
        assert cp_cancel.workflow_status == WorkflowStatus.CANCELLED
        assert cp_cancel.task_records[step_1].status == TaskStatus.CANCELLED
        assert cp_cancel.task_records[step_2].status == TaskStatus.CANCELLED
        assert evt_sink.get_events()[-1].event_type == EventType.WORKFLOW_CANCELLED
        assert evt_sink.get_events()[-1].payload["source"] == "USER"


class TestTransitionCoordinatorValidationAndConsistency:
    def test_illegal_workflow_transitions(self):
        cp_store = InMemoryCheckpointStore()
        evt_sink = InMemoryEventSink()
        coordinator = TransitionCoordinator(cp_store, evt_sink)

        run_id = RunId("run-invalid-wf")
        initial_state = WorkflowState[dict[str, Any]](data={})
        run_coro(coordinator.initialize_run(
            run_id=run_id,
            workflow_id=NonEmptyStr("wf-inv"),
            definition_version=NonEmptyStr("1.0.0"),
            definition_hash=DUMMY_HASH,
            initial_state=initial_state,
        ))

        # PENDING -> COMPLETED directly is illegal
        with pytest.raises(IllegalStateTransitionError, match="cannot transition from 'PENDING' to 'COMPLETED'"):
            run_coro(coordinator.complete_run(run_id))

        # PENDING -> PAUSED directly is illegal
        with pytest.raises(IllegalStateTransitionError, match="cannot transition from 'PENDING' to 'PAUSED'"):
            run_coro(coordinator.pause_run(run_id))

    def test_illegal_task_transitions(self):
        cp_store = InMemoryCheckpointStore()
        evt_sink = InMemoryEventSink()
        coordinator = TransitionCoordinator(cp_store, evt_sink)

        run_id = RunId("run-invalid-task")
        step_1 = StepId("step-1")
        initial_state = WorkflowState[dict[str, Any]](data={})
        run_coro(coordinator.initialize_run(
            run_id=run_id,
            workflow_id=NonEmptyStr("wf-inv-task"),
            definition_version=NonEmptyStr("1.0.0"),
            definition_hash=DUMMY_HASH,
            initial_state=initial_state,
            task_ids=(step_1,),
        ))
        run_coro(coordinator.start_run(run_id))

        # PENDING -> RUNNING directly without READY is illegal
        with pytest.raises(IllegalStateTransitionError, match="cannot transition from 'PENDING' to 'RUNNING'"):
            run_coro(coordinator.mark_task_running(run_id, step_1))

    def test_checkpoint_store_failure_leaves_active_context_untouched(self):
        cp_store = FaultyCheckpointStore(fail_save=True)
        evt_sink = InMemoryEventSink()
        coordinator = TransitionCoordinator(cp_store, evt_sink)

        run_id = RunId("run-cp-fail")
        initial_state = WorkflowState[dict[str, Any]](data={})
        with pytest.raises(CheckpointPersistError, match="Failed to persist checkpoint"):
            run_coro(coordinator.initialize_run(
                run_id=run_id,
                workflow_id=NonEmptyStr("wf-cp-fail"),
                definition_version=NonEmptyStr("1.0.0"),
                definition_hash=DUMMY_HASH,
                initial_state=initial_state,
            ))

        # Event sink was never called
        assert len(evt_sink.get_events()) == 0

    def test_event_sink_failure_rolls_back_checkpoint_safely(self):
        cp_store = InMemoryCheckpointStore()
        evt_sink = FaultyEventSink(fail_record=False)
        coordinator = TransitionCoordinator(cp_store, evt_sink)

        run_id = RunId("run-evt-fail")
        initial_state = WorkflowState[dict[str, Any]](data={})
        # Initialize succeeds
        run_coro(coordinator.initialize_run(
            run_id=run_id,
            workflow_id=NonEmptyStr("wf-evt-fail"),
            definition_version=NonEmptyStr("1.0.0"),
            definition_hash=DUMMY_HASH,
            initial_state=initial_state,
        ))
        assert (run_coro(cp_store.get_latest(run_id))).sequence_number == 0

        # Now inject event sink failure on start_run
        evt_sink.fail_record = True
        with pytest.raises(EventSinkError, match="Event recording failed"):
            run_coro(coordinator.start_run(run_id))

        # Checkpoint 1 was safely rolled back! Latest is still sequence 0
        latest_cp = run_coro(cp_store.get_latest(run_id))
        assert latest_cp is not None
        assert latest_cp.sequence_number == 0

        # Active context sequences and status remain at 0 (PENDING)
        ctx = coordinator.get_active_context(run_id)
        assert ctx["workflow_status"] == WorkflowStatus.PENDING
        assert ctx["current_checkpoint_sequence"] == 0
        assert ctx["current_event_sequence"] == 0
        assert not coordinator.is_quarantined(run_id)

    def test_event_sink_failure_and_rollback_failure_quarantines_run(self):
        cp_store = FaultyCheckpointStore(fail_save=False, fail_rollback=False)
        evt_sink = FaultyEventSink(fail_record=False)
        coordinator = TransitionCoordinator(cp_store, evt_sink)

        run_id = RunId("run-quarantine")
        initial_state = WorkflowState[dict[str, Any]](data={})
        run_coro(coordinator.initialize_run(
            run_id=run_id,
            workflow_id=NonEmptyStr("wf-quarantine"),
            definition_version=NonEmptyStr("1.0.0"),
            definition_hash=DUMMY_HASH,
            initial_state=initial_state,
        ))

        # Inject double failure: event fails AND rollback fails!
        evt_sink.fail_record = True
        cp_store.fail_rollback = True

        with pytest.raises(CoordinatorConsistencyError, match="CRITICAL: Event recording failed.*Run is quarantined"):
            run_coro(coordinator.start_run(run_id))

        assert coordinator.is_quarantined(run_id) is True

        # Any further transition on quarantined run is rejected immediately
        with pytest.raises(CoordinatorConsistencyError, match="Run 'run-quarantine' is quarantined"):
            run_coro(coordinator.pause_run(run_id))

        with pytest.raises(CoordinatorConsistencyError, match="Run 'run-quarantine' is quarantined"):
            run_coro(coordinator.cancel_run(run_id))

    def test_record_event_only_advances_event_stream_independently(self):
        cp_store = InMemoryCheckpointStore()
        evt_sink = InMemoryEventSink()
        coordinator = TransitionCoordinator(cp_store, evt_sink)

        run_id = RunId("run-evt-stream")
        initial_state = WorkflowState[dict[str, Any]](data={})
        run_coro(coordinator.initialize_run(
            run_id=run_id,
            workflow_id=NonEmptyStr("wf-evt-stream"),
            definition_version=NonEmptyStr("1.0.0"),
            definition_hash=DUMMY_HASH,
            initial_state=initial_state,
        ))
        assert coordinator.get_active_context(run_id)["current_checkpoint_sequence"] == 0
        assert coordinator.get_active_context(run_id)["current_event_sequence"] == 0

        # Emit standalone events
        e1 = run_coro(
            coordinator.record_event_only(
                run_id=run_id,
                event_type=EventType.STEP_EXECUTED,
                payload={"level": "INFO", "msg": "Intermediate progress"},
            )
        )
        assert e1.sequence_number == 1
        assert coordinator.get_active_context(run_id)["current_checkpoint_sequence"] == 0
        assert coordinator.get_active_context(run_id)["current_event_sequence"] == 1

        e2 = run_coro(
            coordinator.record_event_only(
                run_id=run_id,
                event_type=EventType.CHECKPOINT_COMMITTED,
                payload={"metric": "cpu_usage", "value": 0.42},
            )
        )
        assert e2.sequence_number == 2
        assert coordinator.get_active_context(run_id)["current_checkpoint_sequence"] == 0
        assert coordinator.get_active_context(run_id)["current_event_sequence"] == 2

    def test_restore_from_checkpoint(self):
        cp_store = InMemoryCheckpointStore()
        evt_sink = InMemoryEventSink()
        coordinator1 = TransitionCoordinator(cp_store, evt_sink)

        run_id = RunId("run-restore")
        step_1 = StepId("step-1")
        initial_state = WorkflowState[dict[str, Any]](data={"val": 10})
        run_coro(coordinator1.initialize_run(
            run_id=run_id,
            workflow_id=NonEmptyStr("wf-restore"),
            definition_version=NonEmptyStr("1.0.0"),
            definition_hash=DUMMY_HASH,
            initial_state=initial_state,
            task_ids=(step_1,),
        ))
        run_coro(coordinator1.start_run(run_id))
        cp_latest = run_coro(cp_store.get_latest(run_id))
        assert cp_latest is not None
        assert cp_latest.sequence_number == 1

        # Simulate fresh coordinator instance recovering from checkpoint
        coordinator2 = TransitionCoordinator(cp_store, evt_sink)
        coordinator2.restore_from_checkpoint(cp_latest)

        ctx2 = coordinator2.get_active_context(run_id)
        assert ctx2["workflow_status"] == WorkflowStatus.RUNNING
        assert ctx2["current_checkpoint_sequence"] == 1

        # Proceed execution on restored coordinator
        cp2 = run_coro(coordinator2.mark_task_ready(run_id, step_1))
        assert cp2.sequence_number == 2
        assert cp2.task_records[step_1].status == TaskStatus.READY

    def test_state_validation_error_prevents_commit(self):
        cp_store = InMemoryCheckpointStore()
        evt_sink = InMemoryEventSink()
        coordinator = TransitionCoordinator(cp_store, evt_sink)

        run_id = RunId("run-state-err")
        step_1 = StepId("step-1")
        initial_state = WorkflowState[dict[str, Any]](data={"pre_existing": 42})
        step_def = create_step_def("step-1", outputs=("declared_out",))

        run_coro(coordinator.initialize_run(
            run_id=run_id,
            workflow_id=NonEmptyStr("wf-state-err"),
            definition_version=NonEmptyStr("1.0.0"),
            definition_hash=DUMMY_HASH,
            initial_state=initial_state,
            task_ids=(step_1,),
        ))
        run_coro(coordinator.start_run(run_id))
        run_coro(coordinator.mark_task_ready(run_id, step_1))
        run_coro(coordinator.mark_task_running(run_id, step_1))

        # Result modifies pre-existing key (forbidden!)
        invalid_result = StepResult[dict[str, Any]](
            step_id=step_1,
            outcome=StepOutcome.SUCCEEDED,
            current_state={"pre_existing": 999, "declared_out": "val"},
        )
        with pytest.raises(StateValidationError, match="illegally modified existing state key"):
            run_coro(coordinator.commit_step_succeeded(run_id, step_1, invalid_result, step_def))

        # Neither checkpoint nor event was committed
        assert coordinator.get_active_context(run_id)["current_checkpoint_sequence"] == 3
        assert coordinator.get_active_context(run_id)["task_records"][step_1].status == TaskStatus.RUNNING


class TestInitializeRunSafety:
    def test_duplicate_initialization_of_running_run_rejected(self):
        cp_store = InMemoryCheckpointStore()
        evt_sink = InMemoryEventSink()
        coordinator = TransitionCoordinator(cp_store, evt_sink)

        run_id = RunId("run-dup-init")
        initial_state = WorkflowState[dict[str, Any]](data={"counter": 1})
        run_coro(coordinator.initialize_run(
            run_id=run_id,
            workflow_id=NonEmptyStr("wf-dup"),
            definition_version=NonEmptyStr("1.0.0"),
            definition_hash=DUMMY_HASH,
            initial_state=initial_state,
        ))
        run_coro(coordinator.start_run(run_id))
        assert coordinator.get_active_context(run_id)["workflow_status"] == WorkflowStatus.RUNNING

        # Attempt to re-initialize existing running run
        with pytest.raises(IllegalStateTransitionError, match="already registered"):
            run_coro(coordinator.initialize_run(
                run_id=run_id,
                workflow_id=NonEmptyStr("wf-dup"),
                definition_version=NonEmptyStr("1.0.0"),
                definition_hash=DUMMY_HASH,
                initial_state=initial_state,
            ))

        # Original context remains strictly intact
        ctx = coordinator.get_active_context(run_id)
        assert ctx["workflow_status"] == WorkflowStatus.RUNNING
        assert ctx["current_checkpoint_sequence"] == 1
        assert ctx["current_event_sequence"] == 1

    def test_duplicate_initialization_of_quarantined_run_rejected(self):
        cp_store = FaultyCheckpointStore(fail_save=False, fail_rollback=True)
        evt_sink = FaultyEventSink(fail_record=False)
        coordinator = TransitionCoordinator(cp_store, evt_sink)

        run_id = RunId("run-dup-q")
        initial_state = WorkflowState[dict[str, Any]](data={})
        run_coro(coordinator.initialize_run(
            run_id=run_id,
            workflow_id=NonEmptyStr("wf-q"),
            definition_version=NonEmptyStr("1.0.0"),
            definition_hash=DUMMY_HASH,
            initial_state=initial_state,
        ))

        # Force quarantine: event fail + rollback fail
        evt_sink.fail_record = True
        with pytest.raises(CoordinatorConsistencyError):
            run_coro(coordinator.start_run(run_id))

        assert coordinator.is_quarantined(run_id) is True

        # Attempt to overwrite quarantined run via initialize_run
        with pytest.raises(CoordinatorConsistencyError, match="is quarantined"):
            run_coro(coordinator.initialize_run(
                run_id=run_id,
                workflow_id=NonEmptyStr("wf-q"),
                definition_version=NonEmptyStr("1.0.0"),
                definition_hash=DUMMY_HASH,
                initial_state=initial_state,
            ))

        # Quarantine remains strictly in place
        assert coordinator.is_quarantined(run_id) is True

    def test_initial_checkpoint_failure_does_not_register_context(self):
        cp_store = FaultyCheckpointStore(fail_save=True)
        evt_sink = InMemoryEventSink()
        coordinator = TransitionCoordinator(cp_store, evt_sink)

        run_id = RunId("run-init-cp-fail")
        initial_state = WorkflowState[dict[str, Any]](data={})
        with pytest.raises(CheckpointPersistError):
            run_coro(coordinator.initialize_run(
                run_id=run_id,
                workflow_id=NonEmptyStr("wf-fail"),
                definition_version=NonEmptyStr("1.0.0"),
                definition_hash=DUMMY_HASH,
                initial_state=initial_state,
            ))

        with pytest.raises(KeyError, match="not registered"):
            coordinator.get_active_context(run_id)

    def test_initial_event_failure_does_not_register_context(self):
        cp_store = InMemoryCheckpointStore()
        evt_sink = FaultyEventSink(fail_record=True)
        coordinator = TransitionCoordinator(cp_store, evt_sink)

        run_id = RunId("run-init-evt-fail")
        initial_state = WorkflowState[dict[str, Any]](data={})
        with pytest.raises(EventSinkError):
            run_coro(coordinator.initialize_run(
                run_id=run_id,
                workflow_id=NonEmptyStr("wf-fail"),
                definition_version=NonEmptyStr("1.0.0"),
                definition_hash=DUMMY_HASH,
                initial_state=initial_state,
            ))

        with pytest.raises(KeyError, match="not registered"):
            coordinator.get_active_context(run_id)


class TestStartRunPreconditions:
    def test_start_run_rejects_non_pending_and_terminal_states(self):
        cp_store = InMemoryCheckpointStore()
        evt_sink = InMemoryEventSink()
        coordinator = TransitionCoordinator(cp_store, evt_sink)

        run_id = RunId("run-start-prec")
        step_1 = StepId("step-1")
        initial_state = WorkflowState[dict[str, Any]](data={})
        step_def = create_step_def("step-1", outputs=("out",))

        run_coro(coordinator.initialize_run(
            run_id=run_id,
            workflow_id=NonEmptyStr("wf-start"),
            definition_version=NonEmptyStr("1.0.0"),
            definition_hash=DUMMY_HASH,
            initial_state=initial_state,
            task_ids=(step_1,),
        ))

        # Start run succeeds from PENDING
        run_coro(coordinator.start_run(run_id))
        assert coordinator.get_active_context(run_id)["workflow_status"] == WorkflowStatus.RUNNING

        # Duplicate start_run from RUNNING is rejected
        with pytest.raises(IllegalStateTransitionError, match="expected 'PENDING'"):
            run_coro(coordinator.start_run(run_id))

        # From PAUSED
        run_coro(coordinator.pause_run(run_id))
        with pytest.raises(IllegalStateTransitionError, match="expected 'PENDING'"):
            run_coro(coordinator.start_run(run_id))

        # Resume to RUNNING
        run_coro(coordinator.resume_run(run_id))

        # From WAITING_FOR_APPROVAL
        run_coro(coordinator.mark_task_ready(run_id, step_1))
        run_coro(coordinator.mark_task_running(run_id, step_1))
        app_req = ApprovalRequest(
            approval_id=ApprovalId("app-1"),
            run_id=run_id,
            step_id=step_1,
            artifact=ArtifactRef(artifact_id=ArtifactId("art-1"), content_hash=DUMMY_HASH, version=1),
            summary=NonEmptyStr("Review"),
        )
        run_coro(coordinator.suspend_for_approval(run_id, step_1, app_req, step_def))
        with pytest.raises(IllegalStateTransitionError, match="expected 'PENDING'"):
            run_coro(coordinator.start_run(run_id))

        # From SUSPENDED (via CHANGES_REQUESTED)
        dec_cr = ApprovalDecision(
            approval_id=ApprovalId("app-1"),
            run_id=run_id,
            step_id=step_1,
            action=DecisionAction.REQUEST_CHANGES,
            reviewer=NonEmptyStr("rev@test.com"),
            artifact_id=ArtifactId("art-1"),
            artifact_version=1,
            artifact_hash_reviewed=DUMMY_HASH,
        )
        run_coro(coordinator.commit_approval_outcome(
            run_id, step_1, ApprovalStatus.CHANGES_REQUESTED, initial_state, dec_cr
        ))
        with pytest.raises(IllegalStateTransitionError, match="expected 'PENDING'"):
            run_coro(coordinator.start_run(run_id))

        # From CANCELLED (terminal)
        run_coro(coordinator.cancel_run(run_id))
        with pytest.raises(WorkflowTerminatedError, match="terminal status 'CANCELLED'"):
            run_coro(coordinator.start_run(run_id))


class TestResumeRunPreconditions:
    def test_resume_run_source_preconditions(self):
        cp_store = InMemoryCheckpointStore()
        evt_sink = InMemoryEventSink()
        coordinator = TransitionCoordinator(cp_store, evt_sink)

        run_id = RunId("run-resume-prec")
        step_1 = StepId("step-1")
        initial_state = WorkflowState[dict[str, Any]](data={})
        step_def = create_step_def("step-1", outputs=("out",))

        run_coro(coordinator.initialize_run(
            run_id=run_id,
            workflow_id=NonEmptyStr("wf-res"),
            definition_version=NonEmptyStr("1.0.0"),
            definition_hash=DUMMY_HASH,
            initial_state=initial_state,
            task_ids=(step_1,),
        ))

        # From PENDING -> rejected
        with pytest.raises(IllegalStateTransitionError, match="expected 'PAUSED' or 'SUSPENDED'"):
            run_coro(coordinator.resume_run(run_id))

        run_coro(coordinator.start_run(run_id))

        # From PAUSED -> succeeds
        run_coro(coordinator.pause_run(run_id))
        cp_res = run_coro(coordinator.resume_run(run_id))
        assert cp_res.workflow_status == WorkflowStatus.RUNNING

        # From WAITING_FOR_APPROVAL -> rejected & pending_approval preserved!
        run_coro(coordinator.mark_task_ready(run_id, step_1))
        run_coro(coordinator.mark_task_running(run_id, step_1))
        app_req = ApprovalRequest(
            approval_id=ApprovalId("app-r"),
            run_id=run_id,
            step_id=step_1,
            artifact=ArtifactRef(artifact_id=ArtifactId("art-1"), content_hash=DUMMY_HASH, version=1),
            summary=NonEmptyStr("Review"),
        )
        run_coro(coordinator.suspend_for_approval(run_id, step_1, app_req, step_def))

        with pytest.raises(IllegalStateTransitionError, match="expected 'PAUSED' or 'SUSPENDED'"):
            run_coro(coordinator.resume_run(run_id))

        # Request preserved, status untouched
        ctx = coordinator.get_active_context(run_id)
        assert ctx["workflow_status"] == WorkflowStatus.WAITING_FOR_APPROVAL
        assert ctx["pending_approval"] is not None
        assert str(ctx["pending_approval"].approval_id) == "app-r"

        # From SUSPENDED -> succeeds
        dec_cr = ApprovalDecision(
            approval_id=ApprovalId("app-r"),
            run_id=run_id,
            step_id=step_1,
            action=DecisionAction.REQUEST_CHANGES,
            reviewer=NonEmptyStr("rev@test.com"),
            artifact_id=ArtifactId("art-1"),
            artifact_version=1,
            artifact_hash_reviewed=DUMMY_HASH,
        )
        run_coro(coordinator.commit_approval_outcome(
            run_id, step_1, ApprovalStatus.CHANGES_REQUESTED, initial_state, dec_cr
        ))
        assert coordinator.get_active_context(run_id)["workflow_status"] == WorkflowStatus.SUSPENDED
        cp_res2 = run_coro(coordinator.resume_run(run_id))
        assert cp_res2.workflow_status == WorkflowStatus.RUNNING

        # From terminal state -> rejected
        run_coro(coordinator.complete_run(run_id))
        with pytest.raises(WorkflowTerminatedError, match="terminal status 'COMPLETED'"):
            run_coro(coordinator.resume_run(run_id))


class TestApprovalCorrelationAndGuards:
    def test_commit_approval_outcome_correlation_and_guards(self):
        cp_store = InMemoryCheckpointStore()
        evt_sink = InMemoryEventSink()
        coordinator = TransitionCoordinator(cp_store, evt_sink)

        run_id = RunId("run-app-guards")
        step_1 = StepId("step-1")
        initial_state = WorkflowState[dict[str, Any]](data={})
        step_def = create_step_def("step-1", outputs=("out",))

        run_coro(coordinator.initialize_run(
            run_id=run_id,
            workflow_id=NonEmptyStr("wf-guards"),
            definition_version=NonEmptyStr("1.0.0"),
            definition_hash=DUMMY_HASH,
            initial_state=initial_state,
            task_ids=(step_1,),
        ))
        run_coro(coordinator.start_run(run_id))
        run_coro(coordinator.mark_task_ready(run_id, step_1))
        run_coro(coordinator.mark_task_running(run_id, step_1))

        dec_dummy = ApprovalDecision(
            approval_id=ApprovalId("app-none"),
            run_id=run_id,
            step_id=step_1,
            action=DecisionAction.APPROVE,
            reviewer=NonEmptyStr("rev@test.com"),
            artifact_id=ArtifactId("art-1"),
            artifact_version=1,
            artifact_hash_reviewed=DUMMY_HASH,
        )

        # 1. Calling commit_approval_outcome when workflow is RUNNING (not WAITING_FOR_APPROVAL)
        with pytest.raises(IllegalStateTransitionError, match="expected 'WAITING_FOR_APPROVAL'"):
            run_coro(coordinator.commit_approval_outcome(
                run_id, step_1, ApprovalStatus.APPROVED, initial_state, dec_dummy
            ))

        # 2. Calling commit_approval_outcome when workflow is PAUSED
        run_coro(coordinator.pause_run(run_id))
        with pytest.raises(IllegalStateTransitionError, match="expected 'WAITING_FOR_APPROVAL'"):
            run_coro(coordinator.commit_approval_outcome(
                run_id, step_1, ApprovalStatus.APPROVED, initial_state, dec_dummy
            ))
        run_coro(coordinator.resume_run(run_id))

        # Suspend for approval
        app_req = ApprovalRequest(
            approval_id=ApprovalId("app-correct"),
            run_id=run_id,
            step_id=step_1,
            artifact=ArtifactRef(artifact_id=ArtifactId("art-1"), content_hash=DUMMY_HASH, version=1),
            summary=NonEmptyStr("Review"),
        )
        run_coro(coordinator.suspend_for_approval(run_id, step_1, app_req, step_def))

        # 3. Decision approval_id mismatch
        dec_mismatch_id = ApprovalDecision(
            approval_id=ApprovalId("app-WRONG"),
            run_id=run_id,
            step_id=step_1,
            action=DecisionAction.APPROVE,
            reviewer=NonEmptyStr("rev@test.com"),
            artifact_id=ArtifactId("art-1"),
            artifact_version=1,
            artifact_hash_reviewed=DUMMY_HASH,
        )
        with pytest.raises(ApprovalContractError, match="approval_id mismatch"):
            run_coro(coordinator.commit_approval_outcome(
                run_id, step_1, ApprovalStatus.APPROVED, initial_state, dec_mismatch_id
            ))

        # 4. Decision step_id mismatch
        dec_mismatch_step = ApprovalDecision(
            approval_id=ApprovalId("app-correct"),
            run_id=run_id,
            step_id=StepId("step-WRONG"),
            action=DecisionAction.APPROVE,
            reviewer=NonEmptyStr("rev@test.com"),
            artifact_id=ArtifactId("art-1"),
            artifact_version=1,
            artifact_hash_reviewed=DUMMY_HASH,
        )
        with pytest.raises(ApprovalContractError, match="step_id mismatch"):
            run_coro(coordinator.commit_approval_outcome(
                run_id, StepId("step-WRONG"), ApprovalStatus.APPROVED, initial_state, dec_mismatch_step
            ))

    def test_suspend_for_approval_invalid_step_id_causes_zero_mutation(self):
        cp_store = InMemoryCheckpointStore()
        evt_sink = InMemoryEventSink()
        coordinator = TransitionCoordinator(cp_store, evt_sink)

        run_id = RunId("run-suspend-err")
        initial_state = WorkflowState[dict[str, Any]](data={})
        step_def = create_step_def("step-nonexistent", outputs=("out",))

        run_coro(coordinator.initialize_run(
            run_id=run_id,
            workflow_id=NonEmptyStr("wf-sus"),
            definition_version=NonEmptyStr("1.0.0"),
            definition_hash=DUMMY_HASH,
            initial_state=initial_state,
            task_ids=(),
        ))
        run_coro(coordinator.start_run(run_id))
        seq_before = coordinator.get_active_context(run_id)["current_checkpoint_sequence"]

        app_req = ApprovalRequest(
            approval_id=ApprovalId("app-ghost"),
            run_id=run_id,
            step_id=StepId("step-nonexistent"),
            artifact=ArtifactRef(artifact_id=ArtifactId("art-1"), content_hash=DUMMY_HASH, version=1),
            summary=NonEmptyStr("Review"),
        )
        with pytest.raises(IllegalStateTransitionError, match="is not a registered task"):
            run_coro(coordinator.suspend_for_approval(
                run_id, StepId("step-nonexistent"), app_req, step_def
            ))

        # Zero mutation
        ctx = coordinator.get_active_context(run_id)
        assert ctx["workflow_status"] == WorkflowStatus.RUNNING
        assert ctx["current_checkpoint_sequence"] == seq_before
        assert ctx["pending_approval"] is None


class TestApprovalAuditPayloadIntegrity:
    def test_approval_decided_audit_event_contains_all_decision_fields(self):
        cp_store = InMemoryCheckpointStore()
        evt_sink = InMemoryEventSink()
        coordinator = TransitionCoordinator(cp_store, evt_sink)

        run_id = RunId("run-audit-decided")
        step_1 = StepId("step-1")
        initial_state = WorkflowState[dict[str, Any]](data={})
        step_def = create_step_def("step-1", outputs=("approved_doc",))

        run_coro(coordinator.initialize_run(
            run_id=run_id,
            workflow_id=NonEmptyStr("wf-audit"),
            definition_version=NonEmptyStr("1.0.0"),
            definition_hash=DUMMY_HASH,
            initial_state=initial_state,
            task_ids=(step_1,),
        ))
        run_coro(coordinator.start_run(run_id))
        run_coro(coordinator.mark_task_ready(run_id, step_1))
        run_coro(coordinator.mark_task_running(run_id, step_1))

        app_req = ApprovalRequest(
            approval_id=ApprovalId("app-123"),
            run_id=run_id,
            step_id=step_1,
            artifact=ArtifactRef(artifact_id=ArtifactId("art-spec"), content_hash=DUMMY_HASH, version=2),
            summary=NonEmptyStr("Review spec v2"),
        )
        run_coro(coordinator.suspend_for_approval(run_id, step_1, app_req, step_def))

        decision = ApprovalDecision(
            approval_id=ApprovalId("app-123"),
            run_id=run_id,
            step_id=step_1,
            action=DecisionAction.APPROVE,
            reviewer=NonEmptyStr("lead-arch@company.com"),
            artifact_id=ArtifactId("art-spec"),
            artifact_version=2,
            artifact_hash_reviewed=DUMMY_HASH,
        )
        run_coro(coordinator.commit_approval_outcome(
            run_id=run_id,
            step_id=step_1,
            status=ApprovalStatus.APPROVED,
            updated_state=initial_state,
            decision=decision,
        ))

        events = evt_sink.get_events_by_type(EventType.APPROVAL_DECIDED)
        assert len(events) == 1
        payload = events[0].payload
        assert payload["approval_id"] == "app-123"
        assert payload["artifact_id"] == "art-spec"
        assert payload["artifact_hash_reviewed"] == str(DUMMY_HASH)
        assert payload["reviewer"] == "lead-arch@company.com"
        assert payload["action"] == "APPROVE"
        assert payload["step_id"] == "step-1"

    def test_approval_rejected_audit_event_contains_all_decision_fields(self):
        cp_store = InMemoryCheckpointStore()
        evt_sink = InMemoryEventSink()
        coordinator = TransitionCoordinator(cp_store, evt_sink)

        run_id = RunId("run-audit-rejected")
        step_1 = StepId("step-1")
        initial_state = WorkflowState[dict[str, Any]](data={})
        step_def = create_step_def("step-1", outputs=("doc",))

        run_coro(coordinator.initialize_run(
            run_id=run_id,
            workflow_id=NonEmptyStr("wf-rej"),
            definition_version=NonEmptyStr("1.0.0"),
            definition_hash=DUMMY_HASH,
            initial_state=initial_state,
            task_ids=(step_1,),
        ))
        run_coro(coordinator.start_run(run_id))
        run_coro(coordinator.mark_task_ready(run_id, step_1))
        run_coro(coordinator.mark_task_running(run_id, step_1))

        app_req = ApprovalRequest(
            approval_id=ApprovalId("app-rej-1"),
            run_id=run_id,
            step_id=step_1,
            artifact=ArtifactRef(artifact_id=ArtifactId("art-rej"), content_hash=DUMMY_HASH, version=1),
            summary=NonEmptyStr("Review"),
        )
        run_coro(coordinator.suspend_for_approval(run_id, step_1, app_req, step_def))

        decision = ApprovalDecision(
            approval_id=ApprovalId("app-rej-1"),
            run_id=run_id,
            step_id=step_1,
            action=DecisionAction.REJECT,
            reviewer=NonEmptyStr("auditor@company.com"),
            artifact_id=ArtifactId("art-rej"),
            artifact_version=1,
            artifact_hash_reviewed=DUMMY_HASH,
        )
        run_coro(coordinator.commit_approval_outcome(
            run_id=run_id,
            step_id=step_1,
            status=ApprovalStatus.REJECTED,
            updated_state=initial_state,
            decision=decision,
        ))

        events = evt_sink.get_events_by_type(EventType.WORKFLOW_CANCELLED)
        assert len(events) == 1
        payload = events[0].payload
        assert payload["source"] == "REVIEWER"
        assert payload["approval_id"] == "app-rej-1"
        assert payload["artifact_id"] == "art-rej"
        assert payload["artifact_hash_reviewed"] == str(DUMMY_HASH)
        assert payload["reviewer"] == "auditor@company.com"
        assert payload["action"] == "REJECT"
        assert payload["step_id"] == "step-1"


class TestRejectionCancellationMixedTasks:
    def test_approval_rejection_cancels_all_active_tasks_preserving_terminal(self):
        cp_store = InMemoryCheckpointStore()
        evt_sink = InMemoryEventSink()
        coordinator = TransitionCoordinator(cp_store, evt_sink)

        run_id = RunId("run-mixed-reject")
        s1 = StepId("step-done")
        s2 = StepId("step-approval")
        s3 = StepId("step-ready")
        s4 = StepId("step-pending")

        initial_state = WorkflowState[dict[str, Any]](data={})
        step_def = create_step_def("step-approval", outputs=("out",))

        run_coro(coordinator.initialize_run(
            run_id=run_id,
            workflow_id=NonEmptyStr("wf-mixed"),
            definition_version=NonEmptyStr("1.0.0"),
            definition_hash=DUMMY_HASH,
            initial_state=initial_state,
            task_ids=(s1, s2, s3, s4),
        ))
        run_coro(coordinator.start_run(run_id))

        # s1: SUCCEEDED
        run_coro(coordinator.mark_task_ready(run_id, s1))
        run_coro(coordinator.mark_task_running(run_id, s1))
        step_res1 = StepResult[dict[str, Any]](
            step_id=s1,
            outcome=StepOutcome.SUCCEEDED,
            current_state={"res1": "done"},
        )
        run_coro(coordinator.commit_step_succeeded(
            run_id, s1, step_res1, create_step_def("step-done", outputs=("res1",))
        ))

        # s2: RUNNING -> WAITING_FOR_APPROVAL
        run_coro(coordinator.mark_task_ready(run_id, s2))
        run_coro(coordinator.mark_task_running(run_id, s2))
        app_req = ApprovalRequest(
            approval_id=ApprovalId("app-mix"),
            run_id=run_id,
            step_id=s2,
            artifact=ArtifactRef(artifact_id=ArtifactId("art-mix"), content_hash=DUMMY_HASH, version=1),
            summary=NonEmptyStr("Review"),
        )
        run_coro(coordinator.suspend_for_approval(run_id, s2, app_req, step_def))

        # s3: READY
        run_coro(coordinator.mark_task_ready(run_id, s3))

        # s4: PENDING (remains PENDING)

        # Reviewer REJECTS s2
        decision = ApprovalDecision(
            approval_id=ApprovalId("app-mix"),
            run_id=run_id,
            step_id=s2,
            action=DecisionAction.REJECT,
            reviewer=NonEmptyStr("qa@company.com"),
            artifact_id=ArtifactId("art-mix"),
            artifact_version=1,
            artifact_hash_reviewed=DUMMY_HASH,
        )
        cp_rej = run_coro(coordinator.commit_approval_outcome(
            run_id=run_id,
            step_id=s2,
            status=ApprovalStatus.REJECTED,
            updated_state=initial_state,
            decision=decision,
        ))

        assert cp_rej.workflow_status == WorkflowStatus.CANCELLED
        # Step 1 was SUCCEEDED -> remains SUCCEEDED
        assert cp_rej.task_records[s1].status == TaskStatus.SUCCEEDED
        # Step 2 was RUNNING (approval) -> cancelled
        assert cp_rej.task_records[s2].status == TaskStatus.CANCELLED
        # Step 3 was READY -> cancelled
        assert cp_rej.task_records[s3].status == TaskStatus.CANCELLED
        # Step 4 was PENDING -> cancelled
        assert cp_rej.task_records[s4].status == TaskStatus.CANCELLED


class TestMetadataSnapshotIsolation:
    def test_metadata_deep_snapshot_isolation(self):
        cp_store = InMemoryCheckpointStore()
        evt_sink = InMemoryEventSink()
        coordinator = TransitionCoordinator(cp_store, evt_sink)

        run_id = RunId("run-meta-iso")
        initial_state = WorkflowState[dict[str, Any]](data={})
        run_coro(coordinator.initialize_run(
            run_id=run_id,
            workflow_id=NonEmptyStr("wf-meta"),
            definition_version=NonEmptyStr("1.0.0"),
            definition_hash=DUMMY_HASH,
            initial_state=initial_state,
        ))

        # Add nested metadata internally
        run_coro(coordinator.start_run(run_id))
        coordinator._runs[str(run_id)].metadata["config"] = {"nested": "original", "items": [1, 2, 3]}

        # Fetch context
        ctx1 = coordinator.get_active_context(run_id)
        assert ctx1["metadata"]["config"]["nested"] == "original"

        # Mutate fetched dictionary in-place
        ctx1["metadata"]["config"]["nested"] = "MUTATED"
        ctx1["metadata"]["config"]["items"].append(999)

        # Re-fetch context from coordinator -> must remain completely unmutated
        ctx2 = coordinator.get_active_context(run_id)
        assert ctx2["metadata"]["config"]["nested"] == "original"
        assert ctx2["metadata"]["config"]["items"] == [1, 2, 3]
