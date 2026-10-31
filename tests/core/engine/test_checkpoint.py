"""Unit tests for CheckpointRecord schema, CheckpointStore protocol, and InMemoryCheckpointStore."""

from datetime import datetime, timedelta, timezone
from typing import Any, Coroutine
import pytest
from pydantic import ValidationError

from src.core.contracts.approval import ApprovalRequest
from src.core.contracts.artifact import ArtifactRef, Sha256Hash
from src.core.contracts.ids import ApprovalId, ArtifactId, NonEmptyStr, RunId, StepId, TaskId
from src.core.contracts.status import ApprovalStatus, TaskStatus, WorkflowStatus
from src.core.contracts.task import TaskRecord
from src.core.engine.checkpoint import (
    CheckpointRecord,
    CheckpointStore,
    InMemoryCheckpointStore,
)
from src.core.engine.errors import (
    CheckpointNotFoundError,
    CheckpointPersistError,
    CheckpointRollbackConflictError,
)
from src.core.workflow.state import WorkflowState

DUMMY_HASH = Sha256Hash("a" * 64)


def run_coro(coro: Coroutine[Any, Any, Any]) -> Any:
    """Run a coroutine to completion synchronously without requiring network/socketpair."""
    try:
        coro.send(None)
    except StopIteration as e:
        return e.value


def create_sample_checkpoint(
    run_id: str = "run-001",
    seq: int = 0,
    status: WorkflowStatus = WorkflowStatus.PENDING,
    state_data: dict[str, Any] | None = None,
) -> CheckpointRecord:
    state = WorkflowState[dict[str, Any]](data={"count": 0} if state_data is None else state_data)
    return CheckpointRecord(
        run_id=RunId(run_id),
        workflow_id=NonEmptyStr("wf-test"),
        definition_version=NonEmptyStr("1.0.0"),
        definition_hash=DUMMY_HASH,
        sequence_number=seq,
        workflow_status=status,
        state_snapshot=state,
        task_records={
            StepId("step-1"): TaskRecord(
                task_id=TaskId("task-step-1"),
                run_id=RunId(run_id),
                status=TaskStatus.PENDING,
            )
        },
        pending_approval=None,
        execution_generations={StepId("step-1"): 1},
        metadata={"env": "test"},
    )


class TestCheckpointRecordSchema:
    def test_checkpoint_record_creation_valid(self):
        rec = create_sample_checkpoint(seq=0)
        assert str(rec.run_id) == "run-001"
        assert rec.sequence_number == 0
        assert rec.workflow_status == WorkflowStatus.PENDING
        assert rec.state_snapshot.data.get("count") == 0
        assert StepId("step-1") in rec.task_records
        assert rec.metadata["env"] == "test"
        assert rec.created_at is not None

    def test_checkpoint_record_frozen_immutability(self):
        rec = create_sample_checkpoint(seq=0)
        with pytest.raises(ValidationError):
            rec.sequence_number = 1  # type: ignore

    def test_checkpoint_record_rejects_negative_sequence(self):
        with pytest.raises(ValidationError):
            create_sample_checkpoint(seq=-1)

    def test_checkpoint_record_extra_forbidden(self):
        with pytest.raises(ValidationError):
            state = WorkflowState[dict[str, Any]](data={"count": 0})
            CheckpointRecord(
                run_id=RunId("run-001"),
                workflow_id=NonEmptyStr("wf-test"),
                definition_version=NonEmptyStr("1.0.0"),
                definition_hash=DUMMY_HASH,
                sequence_number=0,
                workflow_status=WorkflowStatus.PENDING,
                state_snapshot=state,
                task_records={},
                unexpected_field="disallowed",  # type: ignore
            )

    def test_checkpoint_record_with_pending_approval(self):
        state = WorkflowState[dict[str, Any]](data={"count": 0})
        app_req = ApprovalRequest(
            approval_id=ApprovalId("app-123"),
            step_id=StepId("step-review"),
            run_id=RunId("run-001"),
            artifact=ArtifactRef(
                artifact_id=ArtifactId("art-1"),
                content_hash=DUMMY_HASH,
                version=1,
            ),
            summary=NonEmptyStr("Review artifact"),
            status=ApprovalStatus.PENDING,
        )
        rec = CheckpointRecord(
            run_id=RunId("run-001"),
            workflow_id=NonEmptyStr("wf-test"),
            definition_version=NonEmptyStr("1.0.0"),
            definition_hash=DUMMY_HASH,
            sequence_number=1,
            workflow_status=WorkflowStatus.WAITING_FOR_APPROVAL,
            state_snapshot=state,
            pending_approval=app_req,
        )
        assert rec.pending_approval is not None
        assert str(rec.pending_approval.approval_id) == "app-123"

    def test_checkpoint_record_naive_datetime_rejected(self):
        with pytest.raises(ValidationError, match="created_at timestamp must be timezone-aware"):
            CheckpointRecord(
                run_id=RunId("run-001"),
                workflow_id=NonEmptyStr("wf-test"),
                definition_version=NonEmptyStr("1.0.0"),
                definition_hash=DUMMY_HASH,
                sequence_number=0,
                workflow_status=WorkflowStatus.PENDING,
                state_snapshot=WorkflowState[dict[str, Any]](data={}),
                created_at=datetime(2026, 1, 1, 12, 0, 0),  # Naive
            )

    def test_checkpoint_record_aware_non_utc_normalized_to_utc(self):
        tz_plus_5 = timezone(timedelta(hours=5))
        naive_time = datetime(2026, 1, 1, 12, 0, 0, tzinfo=tz_plus_5)
        rec = CheckpointRecord(
            run_id=RunId("run-001"),
            workflow_id=NonEmptyStr("wf-test"),
            definition_version=NonEmptyStr("1.0.0"),
            definition_hash=DUMMY_HASH,
            sequence_number=0,
            workflow_status=WorkflowStatus.PENDING,
            state_snapshot=WorkflowState[dict[str, Any]](data={}),
            created_at=naive_time,
        )
        assert rec.created_at.tzinfo == timezone.utc
        assert rec.created_at == datetime(2026, 1, 1, 7, 0, 0, tzinfo=timezone.utc)

    def test_checkpoint_record_default_timestamp_is_utc_aware(self):
        rec = create_sample_checkpoint(seq=0)
        assert rec.created_at.tzinfo == timezone.utc


class TestInMemoryCheckpointStore:
    def test_store_protocol_conformance(self):
        store = InMemoryCheckpointStore()
        assert isinstance(store, CheckpointStore)

    def test_initial_checkpoint_must_have_sequence_zero(self):
        store = InMemoryCheckpointStore()
        rec_invalid = create_sample_checkpoint(seq=1)
        with pytest.raises(CheckpointPersistError, match="must have sequence_number 0"):
            run_coro(store.save(rec_invalid))

        rec_valid = create_sample_checkpoint(seq=0)
        run_coro(store.save(rec_valid))
        latest = run_coro(store.get_latest("run-001"))
        assert latest is not None
        assert latest.sequence_number == 0

    def test_save_strictly_monotonic_sequences(self):
        store = InMemoryCheckpointStore()
        run_coro(store.save(create_sample_checkpoint(seq=0)))
        run_coro(store.save(create_sample_checkpoint(seq=1)))
        run_coro(store.save(create_sample_checkpoint(seq=2)))

        latest = run_coro(store.get_latest("run-001"))
        assert latest is not None
        assert latest.sequence_number == 2

        history = run_coro(store.list_history("run-001"))
        assert len(history) == 3
        assert [r.sequence_number for r in history] == [0, 1, 2]

    def test_save_rejects_stale_or_duplicate_sequence(self):
        store = InMemoryCheckpointStore()
        run_coro(store.save(create_sample_checkpoint(seq=0)))
        run_coro(store.save(create_sample_checkpoint(seq=1)))

        # Duplicate sequence 1
        with pytest.raises(CheckpointPersistError, match="Stale sequence number 1"):
            run_coro(store.save(create_sample_checkpoint(seq=1)))

        # Older sequence 0
        with pytest.raises(CheckpointPersistError, match="Stale sequence number 0"):
            run_coro(store.save(create_sample_checkpoint(seq=0)))

    def test_save_rejects_sequence_gaps(self):
        store = InMemoryCheckpointStore()
        run_coro(store.save(create_sample_checkpoint(seq=0)))

        # Jump from 0 to 2 (gap: expected 1)
        with pytest.raises(CheckpointPersistError, match="Sequence gap detected: expected 1, got 2"):
            run_coro(store.save(create_sample_checkpoint(seq=2)))

    def test_save_rejects_workflow_id_mismatch(self):
        store = InMemoryCheckpointStore()
        rec0 = CheckpointRecord(
            run_id=RunId("run-001"),
            workflow_id=NonEmptyStr("wf-original"),
            definition_version=NonEmptyStr("1.0.0"),
            definition_hash=DUMMY_HASH,
            sequence_number=0,
            workflow_status=WorkflowStatus.PENDING,
            state_snapshot=WorkflowState[dict[str, Any]](data={}),
        )
        run_coro(store.save(rec0))

        rec1 = CheckpointRecord(
            run_id=RunId("run-001"),
            workflow_id=NonEmptyStr("wf-different"),
            definition_version=NonEmptyStr("1.0.0"),
            definition_hash=DUMMY_HASH,
            sequence_number=1,
            workflow_status=WorkflowStatus.RUNNING,
            state_snapshot=WorkflowState[dict[str, Any]](data={}),
        )
        with pytest.raises(CheckpointPersistError, match="Workflow ID mismatch for run 'run-001'"):
            run_coro(store.save(rec1))

    def test_save_rejects_definition_hash_mismatch(self):
        store = InMemoryCheckpointStore()
        rec0 = CheckpointRecord(
            run_id=RunId("run-001"),
            workflow_id=NonEmptyStr("wf-original"),
            definition_version=NonEmptyStr("1.0.0"),
            definition_hash=Sha256Hash("a" * 64),
            sequence_number=0,
            workflow_status=WorkflowStatus.PENDING,
            state_snapshot=WorkflowState[dict[str, Any]](data={}),
        )
        run_coro(store.save(rec0))

        rec1 = CheckpointRecord(
            run_id=RunId("run-001"),
            workflow_id=NonEmptyStr("wf-original"),
            definition_version=NonEmptyStr("1.0.0"),
            definition_hash=Sha256Hash("b" * 64),
            sequence_number=1,
            workflow_status=WorkflowStatus.RUNNING,
            state_snapshot=WorkflowState[dict[str, Any]](data={}),
        )
        with pytest.raises(CheckpointPersistError, match="Definition hash mismatch for run 'run-001'"):
            run_coro(store.save(rec1))

    def test_save_type_validation(self):
        store = InMemoryCheckpointStore()
        with pytest.raises(TypeError, match="Expected CheckpointRecord"):
            run_coro(store.save({"sequence_number": 0}))  # type: ignore

    def test_get_latest_nonexistent_run(self):
        store = InMemoryCheckpointStore()
        res = run_coro(store.get_latest("nonexistent-run"))
        assert res is None

    def test_get_by_sequence(self):
        store = InMemoryCheckpointStore()
        run_coro(store.save(create_sample_checkpoint(seq=0, state_data={"val": "A"})))
        run_coro(store.save(create_sample_checkpoint(seq=1, state_data={"val": "B"})))
        run_coro(store.save(create_sample_checkpoint(seq=2, state_data={"val": "C"})))

        rec0 = run_coro(store.get_by_sequence("run-001", 0))
        assert rec0 is not None
        assert rec0.state_snapshot.data.get("val") == "A"

        rec1 = run_coro(store.get_by_sequence("run-001", 1))
        assert rec1 is not None
        assert rec1.state_snapshot.data.get("val") == "B"

        rec_missing = run_coro(store.get_by_sequence("run-001", 99))
        assert rec_missing is None

        rec_other_run = run_coro(store.get_by_sequence("run-002", 0))
        assert rec_other_run is None

    def test_list_history_empty_and_populated(self):
        store = InMemoryCheckpointStore()
        assert run_coro(store.list_history("unknown")) == []

        run_coro(store.save(create_sample_checkpoint(seq=0)))
        run_coro(store.save(create_sample_checkpoint(seq=1)))
        history = run_coro(store.list_history("run-001"))
        assert len(history) == 2

    def test_snapshot_isolation_on_save_and_retrieve(self):
        store = InMemoryCheckpointStore()
        mutable_meta = {"key": "original"}
        rec = CheckpointRecord(
            run_id=RunId("run-001"),
            workflow_id=NonEmptyStr("wf-test"),
            definition_version=NonEmptyStr("1.0.0"),
            definition_hash=DUMMY_HASH,
            sequence_number=0,
            workflow_status=WorkflowStatus.PENDING,
            state_snapshot=WorkflowState[dict[str, Any]](data={"counter": 10}),
            metadata=mutable_meta,
        )
        run_coro(store.save(rec))

        # Mutate local dict passed to rec
        mutable_meta["key"] = "mutated_locally"

        # Fetched record must still have original value
        fetched = run_coro(store.get_latest("run-001"))
        assert fetched is not None
        assert fetched.metadata["key"] == "original"

        # Mutate fetched object dict
        fetched_dict = dict(fetched.metadata)
        fetched_dict["key"] = "mutated_after_fetch"

        # Next fetch still unaffected
        fetched_again = run_coro(store.get_latest("run-001"))
        assert fetched_again is not None
        assert fetched_again.metadata["key"] == "original"

    def test_rollback_latest_successful(self):
        store = InMemoryCheckpointStore()
        run_coro(store.save(create_sample_checkpoint(seq=0)))
        run_coro(store.save(create_sample_checkpoint(seq=1)))
        run_coro(store.save(create_sample_checkpoint(seq=2)))

        assert (run_coro(store.get_latest("run-001"))).sequence_number == 2

        # Rollback sequence 2
        run_coro(store.rollback_latest("run-001", expected_sequence_number=2))

        latest = run_coro(store.get_latest("run-001"))
        assert latest is not None
        assert latest.sequence_number == 1

        history = run_coro(store.list_history("run-001"))
        assert len(history) == 2
        assert [r.sequence_number for r in history] == [0, 1]

    def test_rollback_latest_conflict_when_newer_checkpoint_exists(self):
        store = InMemoryCheckpointStore()
        run_coro(store.save(create_sample_checkpoint(seq=0)))
        run_coro(store.save(create_sample_checkpoint(seq=1)))
        run_coro(store.save(create_sample_checkpoint(seq=2)))

        # Attempt to rollback sequence 1 when latest is 2 -> Conflict!
        with pytest.raises(CheckpointRollbackConflictError, match="Cannot rollback sequence 1.*newer sequence 2"):
            run_coro(store.rollback_latest("run-001", expected_sequence_number=1))

        # Checkpoints remain intact
        assert (run_coro(store.get_latest("run-001"))).sequence_number == 2

    def test_rollback_latest_not_found(self):
        store = InMemoryCheckpointStore()
        # Empty store
        with pytest.raises(CheckpointNotFoundError, match="No checkpoints found"):
            run_coro(store.rollback_latest("run-001", expected_sequence_number=0))

        # Store with sequence 0, trying to rollback sequence 1
        run_coro(store.save(create_sample_checkpoint(seq=0)))
        with pytest.raises(CheckpointNotFoundError, match="latest checkpoint has sequence 0"):
            run_coro(store.rollback_latest("run-001", expected_sequence_number=1))

    def test_multiple_runs_isolation(self):
        store = InMemoryCheckpointStore()
        run_coro(store.save(create_sample_checkpoint(run_id="run-A", seq=0)))
        run_coro(store.save(create_sample_checkpoint(run_id="run-A", seq=1)))

        run_coro(store.save(create_sample_checkpoint(run_id="run-B", seq=0)))

        assert (run_coro(store.get_latest("run-A"))).sequence_number == 1
        assert (run_coro(store.get_latest("run-B"))).sequence_number == 0

        run_coro(store.rollback_latest("run-A", expected_sequence_number=1))
        assert (run_coro(store.get_latest("run-A"))).sequence_number == 0
        assert (run_coro(store.get_latest("run-B"))).sequence_number == 0

    def test_clear_store(self):
        store = InMemoryCheckpointStore()
        run_coro(store.save(create_sample_checkpoint(seq=0)))
        store.clear()
        assert run_coro(store.get_latest("run-001")) is None
        assert run_coro(store.list_history("run-001")) == []
