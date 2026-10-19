from __future__ import annotations

from datetime import datetime, timezone

from tally.log.memory import InMemoryRecordStore
from tally.log.records import make_record


def _decision_record(
    trace_id: str = "t1",
    span_id: str = "s1",
    customer_id: str = "cust-1",
    policy_name: str = "p",
    decided_at: str = "2024-06-01T12:00:00Z",
) -> object:
    return make_record(
        type="DECISION",
        trace_id=trace_id,
        span_id=span_id,
        customer_id=customer_id,
        policy_name=policy_name,
        policy_version="v1",
        policy_hash="h1",
        payload={"decision": "BILL", "units": 1, "decided_at": decided_at},
    )


def test_write_and_list_all() -> None:
    store = InMemoryRecordStore()
    r = _decision_record()
    store.write(r)  # type: ignore[arg-type]
    assert store.list() == [r]


def test_list_by_trace_and_span() -> None:
    store = InMemoryRecordStore()
    r1 = _decision_record(trace_id="t1", span_id="s1")
    r2 = _decision_record(trace_id="t2", span_id="s2")
    store.write(r1)  # type: ignore[arg-type]
    store.write(r2)  # type: ignore[arg-type]
    assert store.list(trace_id="t1", span_id="s1") == [r1]


def test_list_by_type() -> None:
    store = InMemoryRecordStore()
    r1 = _decision_record()
    r2 = make_record(
        type="EMISSION",
        trace_id="t1",
        span_id="s1",
        customer_id="cust-1",
        policy_name="p",
        policy_version="v1",
        policy_hash="h1",
        payload={"stripe_identifier": "t1::s1", "shadow": False},
    )
    store.write(r1)  # type: ignore[arg-type]
    store.write(r2)
    assert store.list(type="DECISION") == [r1]
    assert store.list(type="EMISSION") == [r2]


def test_latest_cursor_returns_epoch_when_empty() -> None:
    store = InMemoryRecordStore()
    cursor = store.latest_cursor("p")
    assert cursor == datetime(1970, 1, 1, tzinfo=timezone.utc)


def test_latest_cursor_returns_max_decided_at() -> None:
    store = InMemoryRecordStore()
    store.write(_decision_record(decided_at="2024-06-01T10:00:00Z"))  # type: ignore[arg-type]
    store.write(_decision_record(decided_at="2024-06-01T12:00:00Z", span_id="s2"))  # type: ignore[arg-type]
    cursor = store.latest_cursor("p")
    assert cursor == datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc)


def test_latest_cursor_ignores_other_policies() -> None:
    store = InMemoryRecordStore()
    store.write(_decision_record(policy_name="p", decided_at="2024-06-01T12:00:00Z"))  # type: ignore[arg-type]
    store.write(_decision_record(policy_name="q", decided_at="2024-06-02T12:00:00Z", span_id="s2"))  # type: ignore[arg-type]
    assert store.latest_cursor("p") == datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc)


def test_write_is_idempotent_for_same_content_hash() -> None:
    """Writing the same record twice does not create a duplicate."""
    store = InMemoryRecordStore()
    r = _decision_record()
    store.write(r)  # type: ignore[arg-type]
    store.write(r)  # type: ignore[arg-type]
    assert len(store.list()) == 1
