"""Unit tests for ExecutionEvent, secret sanitization, and InMemoryEventSink."""

from typing import Any, Coroutine
import pytest
from pydantic import ValidationError

from src.core.contracts.ids import NonEmptyStr, RunId
from src.core.engine.events import (
    EventSink,
    EventType,
    ExecutionEvent,
    InMemoryEventSink,
    sanitize_payload,
)

DUMMY_HASH = "b" * 64


def run_coro(coro: Coroutine[Any, Any, Any]) -> Any:
    """Run a coroutine to completion synchronously without requiring network/socketpair."""
    try:
        coro.send(None)
    except StopIteration as e:
        return e.value


def create_event(
    event_type: EventType = EventType.WORKFLOW_STARTED,
    sequence_number: int = 0,
    run_id: str = "run-100",
    payload: dict[str, object] | None = None,
) -> ExecutionEvent:
    return ExecutionEvent(
        event_id=f"evt-{sequence_number}",
        run_id=RunId(run_id),
        workflow_id=NonEmptyStr("wf-test"),
        workflow_version=NonEmptyStr("1.0.0"),
        definition_hash=DUMMY_HASH,
        sequence_number=sequence_number,
        event_type=event_type,
        payload=payload or {},
    )


class TestSecretSanitization:
    def test_redacts_sensitive_keys(self):
        raw = {
            "api_key": "secret_value_123",
            "password": "my_password",
            "auth_token": "bearer xyz",
            "client_secret": "topsecret",
            "private_key": "-----BEGIN KEY-----",
            "user_credential": "cred",
            "safe_field": "visible",
            "count": 42,
        }
        sanitized = sanitize_payload(raw)
        assert sanitized["api_key"] == "[REDACTED]"
        assert sanitized["password"] == "[REDACTED]"
        assert sanitized["auth_token"] == "[REDACTED]"
        assert sanitized["client_secret"] == "[REDACTED]"
        assert sanitized["private_key"] == "[REDACTED]"
        assert sanitized["user_credential"] == "[REDACTED]"
        assert sanitized["safe_field"] == "visible"
        assert sanitized["count"] == 42

    def test_nested_sanitization(self):
        raw = {
            "outer": {
                "nested_token": "token123",
                "normal": "ok",
                "deeper": {"db_password": "pass"},
            },
            "list_items": [
                {"secret_id": "s1"},
                {"public_id": "p1"},
            ],
        }
        sanitized = sanitize_payload(raw)
        assert sanitized["outer"]["nested_token"] == "[REDACTED]"
        assert sanitized["outer"]["normal"] == "ok"
        assert sanitized["outer"]["deeper"]["db_password"] == "[REDACTED]"
        assert sanitized["list_items"][0]["secret_id"] == "[REDACTED]"
        assert sanitized["list_items"][1]["public_id"] == "p1"


class TestExecutionEventModel:
    def test_valid_event_creation(self):
        evt = create_event(
            event_type=EventType.TASK_STARTED,
            sequence_number=1,
            payload={"step": "step-1", "user_token": "abc"},
        )
        assert evt.event_type == EventType.TASK_STARTED
        assert evt.sequence_number == 1
        assert evt.payload["step"] == "step-1"
        assert evt.payload["user_token"] == "[REDACTED]"

    def test_event_immutability(self):
        evt = create_event()
        with pytest.raises(ValidationError):
            evt.sequence_number = 99  # type: ignore[misc]

    def test_negative_sequence_number_rejected(self):
        with pytest.raises(ValidationError):
            create_event(sequence_number=-1)

    def test_non_json_payload_rejected(self):
        with pytest.raises(ValueError):
            create_event(payload={"nan": float("nan")})


class TestInMemoryEventSink:
    def test_protocol_conformance(self):
        sink = InMemoryEventSink()
        assert isinstance(sink, EventSink)

    def test_record_successful_event(self):
        sink = InMemoryEventSink()
        evt = create_event(sequence_number=0)

        run_coro(sink.record(evt))
        assert len(sink) == 1
        events = sink.get_events()
        assert len(events) == 1
        assert events[0].event_id == "evt-0"

    def test_all_or_nothing_atomicity_on_invalid_type(self):
        sink = InMemoryEventSink()
        run_coro(sink.record(create_event(sequence_number=0)))
        assert len(sink) == 1

        with pytest.raises(TypeError):
            run_coro(sink.record("not_an_event"))  # type: ignore[arg-type]

        # Verify sink is untouched after failed record
        assert len(sink) == 1

    def test_caller_mutation_does_not_mutate_stored_event(self):
        sink = InMemoryEventSink()
        payload = {"data": {"nested": "original"}}
        evt = create_event(payload=payload)

        run_coro(sink.record(evt))

        # Mutate caller payload
        payload["data"]["nested"] = "MUTATED"

        # Stored event payload remains untouched
        stored = sink.get_events()[0]
        assert stored.payload["data"]["nested"] == "original"

    def test_sequential_event_ordering_and_filtering(self):
        sink = InMemoryEventSink()
        e0 = create_event(event_type=EventType.WORKFLOW_STARTED, sequence_number=0, run_id="run-1")
        e1 = create_event(event_type=EventType.TASK_STARTED, sequence_number=1, run_id="run-1")
        e2 = create_event(event_type=EventType.TASK_SUCCEEDED, sequence_number=2, run_id="run-1")
        e_other = create_event(event_type=EventType.WORKFLOW_STARTED, sequence_number=0, run_id="run-2")

        run_coro(sink.record(e0))
        run_coro(sink.record(e1))
        run_coro(sink.record(e2))
        run_coro(sink.record(e_other))

        assert len(sink) == 4

        # Ordering check
        run1_events = sink.get_events("run-1")
        assert [e.sequence_number for e in run1_events] == [0, 1, 2]

        # Filter by type
        started = sink.get_events_by_type(EventType.WORKFLOW_STARTED)
        assert len(started) == 2
        assert [e.run_id for e in started] == ["run-1", "run-2"]

        sink.clear()
        assert len(sink) == 0
