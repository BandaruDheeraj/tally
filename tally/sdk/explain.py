from __future__ import annotations

from tally.log.records import Record
from tally.log.store import RecordStore


def explain(trace_id: str, span_id: str, *, store: RecordStore) -> dict[str, object]:
    """Assemble audit trail for a span from its records."""
    span_records = store.list(trace_id=trace_id, span_id=span_id)

    decision_record: Record | None = next(
        (r for r in span_records if r.type == "DECISION"), None
    )
    emission_record: Record | None = next(
        (r for r in span_records if r.type == "EMISSION"), None
    )
    ruling_record: Record | None = next(
        (r for r in span_records if r.type == "RULING"), None
    )

    result: dict[str, object] = {}

    if decision_record is not None:
        result["decision"] = dict(decision_record.payload)
        policy_records = store.list(type="POLICY")
        policy_record: Record | None = next(
            (
                r for r in policy_records
                if r.policy_name == decision_record.policy_name
                and r.policy_version == decision_record.policy_version
            ),
            None,
        )
        if policy_record is not None:
            result["policy"] = dict(policy_record.payload)

    if emission_record is not None:
        result["emission"] = dict(emission_record.payload)

    if ruling_record is not None:
        result["ruling"] = dict(ruling_record.payload)

    return result
